library(data.table)
library(tidyverse)

gdc_clinical_data <- fread("data/gdc-clinical_patient_blca/eaa71705-960a-4abd-b5d7-f5fdc0d0c5af/nationwidechildrens.org_clinical_patient_blca.txt") %>%
  filter(!bcr_patient_uuid %in% c("bcr_patient_uuid", "CDE_ID:")) %>%
  # fix case consistency across clinical data and annotations
  mutate(bcr_patient_uuid = str_to_upper(bcr_patient_uuid))

gdc_annotations <- fread("data/gdc-clinical_patient_blca/eaa71705-960a-4abd-b5d7-f5fdc0d0c5af/annotations.txt") %>%
  select(bcr_patient_uuid = entity_id, note_category, notes) %>%
  mutate(bcr_patient_uuid = str_to_upper(bcr_patient_uuid))

samples_by_bcr_patient <- gdc_annotations$bcr_patient_uuid %>% table()

if (any(samples_by_bcr_patient > 1)) {
  multiple_sample_patients <- samples_by_bcr_patient[samples_by_bcr_patient > 1]
  
  warning(glue::glue(
    "Patients with more than one sample in `gdc_annotations`:\n{paste(names(multiple_sample_patients), multiple_sample_patients, sep = ' = ', collapse = ';\n')}"
  ))
}

gdc_annotations_wide <- gdc_annotations %>%
  group_by(bcr_patient_uuid) %>%
  summarize(category = paste0(category, collapse = "; "),
            notes = paste0(notes, collapse = "; "))

stopifnot("Not al samples from gdc annotations are present in clinical data" = all(gdc_annotations$bcr_patient_uuid %in% gdc_clinical_data$bcr_patient_uuid))

# case_to_drop <- gdc_annotations %>%
#  filter(category %in% c("Item does not meet study protocol", "Item may not meet study protocol"))

llm_data <- fread("processed/blca_local/exports/bladder_features.csv")
samples_by_patient <- llm_data$case_id %>% table()

if (any(samples_by_patient > 1)) {
  multiple_sample_patients <- samples_by_patient[samples_by_patient > 1]
  
  warning(glue::glue(
    "Patients with more than one sample in `llm_data`:\n{paste(names(multiple_sample_patients), multiple_sample_patients, sep = ' = ', collapse = ';\n')}"
  ))
}

# Note for patient TCGA-DK-A1A6: two different reports but reporting the same information for the same surgical specimen (bladder + sigmoid mass)
# drop one of the two duplicate reports
llm_data <- llm_data %>%
  filter(report_id != "c1510f91-9865-4c17-80ac-1dfd3bf80a1a")

comparison <- full_join(
  gdc_clinical_data %>% select(bcr_patient_barcode, bcr_patient_uuid,
                               grade.gdc = neoplasm_histologic_grade,
                               pT.gdc = ajcc_tumor_pathologic_pt, pN.gdc = ajcc_nodes_pathologic_pn,  pM.gdc = ajcc_metastasis_pathologic_pm,
                               LVI.gdc = lymphovascular_invasion) %>%
    mutate(grade.gdc = str_remove(grade.gdc, " Grade")) %>%
    mutate(pN.gdc = recode_values(pN.gdc, "[Not Available]" ~"NX", default = pN.gdc)),
  llm_data %>% select(bcr_patient_barcode = case_id,
                      grade.llm = grade,
                      pT.llm = pT, pN.llm = pN, pM.llm = pM, LVI.llm = vascular_invasion)
) %>% left_join(
  gdc_annotations_wide
)
  mutate(discrepant_pT = pT.gdc != pT.llm,

         discrepant_LVI = LVI.gdc != LVI.llm
         )

# Tumor grade ------------------------------------------------------------------
  
xtabs(~ grade.gdc + grade.llm,
        data = comparison)
  
grade_disrepant <- comparison %>%
  filter(grade.gdc != grade.llm)

# pT ---------------------------------------------------------------------------
xtabs(~ pT.gdc + pT.llm,
      data = comparison)

pT_discrepant <- comparison %>%
  filter(pT.gdc != pT.llm) %>%
  select(-starts_with("pN"), -starts_with("pM"), -starts_with("grade"), -starts_with("LVI"))

# evaluate only discrepancies at the level of mayor t, not individual letters
pT_major_discrepant <- pT_discrepant %>%
  # i ignored gdc upstaged reports because could be due to additional clinical informations or separate samples
  filter(str_extract(pT.gdc, "T[0-9X]") != str_extract(pT.llm, "T[0-9X]"))

pT_minor_discrepant <- pT_discrepant %>%
  filter(!bcr_patient_uuid %in% pT_major_discrepant$bcr_patient_uuid)
  
pT_not_available <- pT_discrepant %>%
  filter(pT.gdc == "[Not Available]")

# pN ---------------------------------------------------------------------------
xtabs(~ pN.gdc + pN.llm,
      data = comparison)

# In cases where the pathology report indicated pN0 or pNX/no nodal examination,
# but GDC reported node-positive disease, the GDC classification was retained.
# Nodal involvement may have been assessed in a separate specimen or procedure
# not represented in the available pathology report; therefore, absence of nodal
# involvement in a given report was not considered sufficient evidence to override
# a documented positive nodal status.
# Note: The LLM frequently flagged discrepancies between the reported number of
# positive lymph nodes and the assigned pN category. These discrepancies may reflect
# differences between AJCC editions. In particular, AJCC 6th-edition nodal staging
# incorporated the size of nodal metastases, whereas later editions placed greater
# emphasis on the number and anatomical location of involved nodes. Therefore, pN
# categories should be interpreted according to the AJCC edition used at the time
# of diagnosis. For this reason I will evaluate node status as simply as N0 vs N+

pN_disrepant <- comparison %>%
  filter(pN.gdc != pN.llm) %>%
  select(-starts_with("pT"), -starts_with("pM"), -starts_with("grade"), -starts_with("LVI")) %>%
  # ignore cases where nodes were not indicated in the report or not identified by the LLM
  filter(pN.llm != "NX") %>%
  filter(!(pN.gdc %in% c("N1", "N2", "N3") & pN.llm %in% c("N1", "N2", "N3"))) %>%
  # sometimes TCGA give NX even if the report indicates N0 because not enoguh lymphnodes were sampled (see TCGA-ZF-A9R0 example)
  filter(!(pN.gdc == "NX" & pN.llm == "N0"))

# pM ---------------------------------------------------------------------------
xtabs(~ pM.gdc + pM.llm,
      data = comparison)

pM_discrepant <- comparison %>%
  filter(pM.gdc != pM.llm) %>%
  select(-starts_with("pT"), -starts_with("pN"), -starts_with("grade"), -starts_with("LVI")) %>%
  filter(pM.llm != "MX") %>%
  filter(!(pM.gdc == "MX" & pM.llm == "M0"))

# LVI ---------------------------------------------------------------------------
xtabs(~ LVI.gdc + LVI.llm,
      data = comparison)

LVI_discrepant <- comparison %>%
  mutate(LVI.gdc = recode_values(
      LVI.gdc,
      c("[Not Available]", "[Not Reported]") ~ "Unknown",
      "NO" ~ "Absent",
      "YES" ~ "Present",
      default = "Unknown"),
    LVI.llm = ifelse(LVI.llm == "", "Unknown", LVI.llm)
    ) %>%
  filter(LVI.gdc != LVI.llm,
         LVI.llm != "Unknown",
         LVI.llm != "Unknown") %>%
  select(-starts_with("pT"), -starts_with("pN"),-starts_with("pM"),  -starts_with("grade"))
  


# for fast exloring of clinical and pathological pT
gdc_clinical_subset_of_interest <- gdc_clinical_data %>%
  select(bcr_patient_uuid, bcr_patient_barcode,
         # histologic_subtype,histological_type, neoplasm_histologic_grade,
         ajcc_staging_edition,
         ajcc_tumor_pathologic_pt, lymphovascular_invasion, ajcc_nodes_pathologic_pn, lymph_nodes_examined, lymph_nodes_examined_count, lymph_nodes_examined_he_count,
         extracapsular_extension, extracapsular_extension_present, ajcc_metastasis_pathologic_pm, metastasis_site, metastatic_site_other, ajcc_pathologic_tumor_stage,
         clinical_M, clinical_N, clinical_T, clinical_stage, stage_other)

