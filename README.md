# TCGA - Pathology Report Parsing with LLM
## Overview
This project automates the processing of pathology reports from The Cancer Genome Atlas (TCGA) BRCA dataset  by combining three primary functions:

1. Downloading Reports: Uses a Bash script to download pathology reports via gdc-client based on a provided manifest.

2. OCR Processing: Utilizes the Google Cloud Vision API to perform Optical Character Recognition (OCR) on the downloaded reports, extracting text from PDFs.

3. Tumor Grade Extraction: Employs a Large Language Model (LLM) (OpenAI o3-mini) to parse the OCR output and extract tumor grade information.


## File Descriptions
`00-download_pathology_reports.sh`
A Bash script that downloads pathology reports using gdc-client from a manifest file.

`01-OCR_with_Google_Vision_API.py`
A Python script that applies the Google Cloud Vision API to perform OCR on the downloaded pathology reports, converting images or PDFs into machine-readable text.
Note: The OCR provided in the original reports is suboptimal.

`02-Extract_tumor_grade_with_LLM-o3_mini.py`
A Python script that processes the extracted text with a Large Language Model (LLM) to identify and extract the tumor grade from the report content.

## LLM Prompt
Below is the LLM prompt template used in this project to extract the tumor grade from the OCR results:

> 
> You are a specialized AI tool with background knowledge in surgical pathology and you help physicians extract relevant information from pathology reports of breast cancer patients.
> You are aware that the text you are provided has been extracted via OCR from a scan of the original file and may contain typos. You will use your knowledge and reasoning to ensure reliable results.
> You will extract the following **three fields** and return them as a single-line **CSV string**, with no headers, and no additional text or commentary:
> 
> 1. **Tumor Histology**
>    Objective: Identify and extract the tumor type according to WHO classifications from the pathology report.
>    Examples: "Infiltrating ductal carcinoma NOS", "Mucinous carcinoma", "Fibroadenoma", etc.
>    Guidelines:
>     - Prioritize exact matches or well-recognized synonyms.
>     - Don't omit relevant description of the histology (e.g. focal)
>     - If multiple lesions are present, prioritize the histology of the invasive lesion.
>     - If multiple invasive lesions are present, indicate the histology of both if different and add a comment.
> 
> 2. **Tumor Grade**
>    Objective: Extract and report the Nottingham histologic grade.
>     Reporting Format:
>     - Use "G1", "G2", "G3" as the grade or "NA" if the grade is unavailable.
>       Accept equivalent designations such as "Elston-Ellis", "SBR", or "Scarff-Bloom-Richardson".
>     
>     Special Considerations:
>     - The grade may appear as a range (e.g., "G1-3" or "GI-III") typically near the end of the report.
>     - If only the Nottingham score is provided (a numerical value ranging from 3 to 9), convert the score as follows: Scores 3–5 → "G1"; Scores 6–7 → "G2"; Scores 8–9 → "G3"
>     - Do not confuse this overall grading with the individual parameters (such as tubule formation, histologic grade, nuclear pleomorphism, or nuclear grade).
>     - Do not infer the grade if only qualitative terms (e.g., "Well differentiated" or "Poorly differentiated") are provided; in these cases, write "NA" and add a comment in the 3rd column.
>     - If multiple lesions are present, indicate the grade of the invasive lesion.
>     - If multiple invasive lesions are present, indicate the grade of both and add a comment.
> 
> 3. **Comments**
>    Objective: Record any uncertainties regarding the tumor grade.
>    Instructions:
>     - If the grade extraction is uncertain (for example, encountering unexpected values such as "G4", ambiguous ranges like "G2-3", or if the grade was inferred from a Nottingham score), provide a brief explanation of the uncertainty.
>     - Include the original text snippet from the report that led to the uncertainty.
>     - If multiple invasive lesions with different grades or histologies are present, report the histology and grade of both lesions.
>     - If the grade is clearly stated and unambiguous, leave this field empty.
> 
> ### Output Format:
> 
> - Return exactly **one line** of **CSV-formatted text** with **three fields**.
> - **If a field contains commas, remove them** to preserve the CSV structure.
> - The output line must contain exactly **two commas** (`,`) in total.
> - Strip excessive whitespace and avoid line breaks inside fields.
> - Do NOT include any header row or extra explanation.
> - **format** the grade using Arabic numerals. The only options for outputting the grade are "G1", "G2", "G3", or "NA" when not available.
> 
> ### Example Output:
> 
> `Infiltrating ductal carcinoma NOS,G1,Multiple lesions; grade inferred from Notthingam score = 4/9`


## Environment Setup

```bash
conda create env -n pdf_ocr python=3.3.0
conda activate pdf_ocr

# Install the required packages:
pip install --upgrade google-cloud-documentai openai
conda install bioconda::gdc-client

# Add your OpenAI API Key to .bashrc or .bash_profile:
export OPENAI_API_KEY="your_api_key"

# Ensure you are logged in to your Google Cloud account by running:
gcloud auth login
```
