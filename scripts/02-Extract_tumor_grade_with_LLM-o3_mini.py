"""
This script processes a TSV file that contains TCGA patient IDs and pathology report transcriptions.
For each patient (each line) it extracts the tumor grade and histology from the report.
The extracted tumor grades, along with the corresponding patient IDs, are then written to an output CSV file.
To handle unexpected interruptions, the script checks if the output files already exist, and if so,
it skips processing the patients whose data have already been processed.
"""

######################
#
#  - Expected cost ~ 5$ for 5h
#  - Optimize in a Json input file to make a batch request (half the price, result in 24h)
# 
#######################

import os
import openai
import csv
import re
import time

openai.api_key = os.environ["OPENAI_API_KEY"]
client = openai.OpenAI()

# Initialize your directory paths
INPUT_DIR = "./processed/pathology_report_transcript/" 
OUTPUT_CSV = "./results/pathology_report-o3_mini.csv"

# Ensure output directory exists
os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

MODEL="o3-mini-2025-01-31"

INSTRUCTION = """
You are a specialized AI tool with background knowledge in surgical pathology and you help physicians extract relevant information from pathology reports of breast cancer patients.
You are aware that the text you are provided has been extracted via OCR from a scan of the original file and may contain typos. You will use your knowledge and reasoning to ensure reliable results.
You will extract the following **three fields** and return them as a single-line **CSV string**, with no headers, and no additional text or commentary:

1. **Tumor Histology**
   Objective: Identify and extract the tumor type according to WHO classifications from the pathology report.
   Examples: "Infiltrating ductal carcinoma NOS", "Mucinous carcinoma", "Fibroadenoma", etc.
   Guidelines:
    - Prioritize exact matches or well-recognized synonyms.
    - Don't omit relevant description of the histology (e.g. focal)
    - If multiple lesions are present, prioritize the histology of the invasive lesion.
    - If multiple invasive lesions are present, indicate the histology of both if different and add a comment.

2. **Tumor Grade**
   Objective: Extract and report the Nottingham histologic grade.
    Reporting Format:
    - Use "G1", "G2", "G3" as the grade or "NA" if the grade is unavailable.
      Accept equivalent designations such as "Elston-Ellis", "SBR", or "Scarff-Bloom-Richardson".
    
    Special Considerations:
    - The grade may appear as a range (e.g., "G1-3" or "GI-III") typically near the end of the report.
    - If only the Nottingham score is provided (a numerical value ranging from 3 to 9), convert the score as follows: Scores 3–5 → "G1"; Scores 6–7 → "G2"; Scores 8–9 → "G3"
    - Do not confuse this overall grading with the individual parameters (such as tubule formation, histologic grade, nuclear pleomorphism, or nuclear grade).
    - Do not infer the grade if only qualitative terms (e.g., "Well differentiated" or "Poorly differentiated") are provided; in these cases, write "NA" and add a comment in the 3rd column.
    - If multiple lesions are present, indicate the grade of the invasive lesion.
    - If multiple invasive lesions are present, indicate the grade of both and add a comment.

3. **Comments**
   Objective: Record any uncertainties regarding the tumor grade.
   Instructions:
    - If the grade extraction is uncertain (for example, encountering unexpected values such as "G4", ambiguous ranges like "G2-3", or if the grade was inferred from a Nottingham score), provide a brief explanation of the uncertainty.
    - Include the original text snippet from the report that led to the uncertainty.
    - If multiple invasive lesions with different grades or histologies are present, report the histology and grade of both lesions.
    - If the grade is clearly stated and unambiguous, leave this field empty.

### Output Format:

- Return exactly **one line** of **CSV-formatted text** with **three fields**.
- **If a field contains commas, remove them** to preserve the CSV structure.
- The output line must contain exactly **two commas** (`,`) in total.
- Strip excessive whitespace and avoid line breaks inside fields.
- Do NOT include any header row or extra explanation.
- **format** the grade using Arabic numerals. The only options for outputting the grade are "G1", "G2", "G3", or "NA" when not available.

### Example Output:

Infiltrating ductal carcinoma NOS,G1,Multiple lesions; grade inferred from Notthingam score = 4/9
"""
# Prompt
PROMPT = """
Please extract the Tumor Histology and Tumor Grade from the provided transcription of the pathology report of a breast cancer patient. Follow the output formatting rules strictly.
"""

# Column headers for the local CSV
COLUMNS = [
    "Patient ID",
    "Tumor Histology",
    "Tumor Grade",
    "Comments"
]

# Read any already processed patient IDs (if the file exists)
processed_ids = set()
if os.path.exists(OUTPUT_CSV):
    with open(OUTPUT_CSV, "r", newline="", encoding="utf-8") as incsv:
        reader = csv.DictReader(incsv)
        for line in reader:
            processed_ids.add(line["Patient ID"])
else:
    # If file doesn't exist, create it and write headers
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as outcsv:
        writer = csv.writer(outcsv)
        writer.writerow(COLUMNS)

# Now open the CSV in append mode to write new results
with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as outcsv:
    writer = csv.writer(outcsv)

    # Loop over each file in the input directory
    for filename in os.listdir(INPUT_DIR):
        # Proceed only if the file matches the pattern TCGA*.txt
        if filename.startswith("TCGA") and filename.endswith(".txt"):
            patient_id = re.search(r"(TCGA-[A-Z0-9]{2}-[A-Z0-9]+)", filename).group(1)
            
            # Skip already processed case IDs
            if patient_id in processed_ids:
                print(f"Skipping already processed ID: {patient_id}")
                continue

            file_path = os.path.join(INPUT_DIR, filename)
            print(f"Processing file: {filename}")

            # Read the entire text from the file
            try:
                with open(file_path, "r", encoding="utf-8") as infile:
                    report_text = infile.read()
            except Exception as e:
                print(f"Error reading file {filename}: {e}")
                continue

            # Call the ChatGPT API with the report text and prompt
            try:
                completion = client.chat.completions.create(
                    model=MODEL,
                    store=False,
                    seed=17,
                    messages=[
                        {
                            "role": "developer",
                            "content": [
                                {
                                    "type": "text",
                                    "text": INSTRUCTION,
                                },
                            ]
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": report_text,
                                },
                                {
                                    "type": "text",
                                    "text": PROMPT,
                                },
                            ]
                        }
                    ],
                )
            except Exception as e:
                print(f"Error processing patient {patient_id}: {e}")
                continue

            # Retrieve the answer from ChatGPT
            answer = completion.choices[0].message.content.strip()

            # Parse the model's CSV output
            csv_lines = answer.split("\n")
            csv_rows = list(csv.reader(csv_lines))
            if len(csv_rows) == 1:
                data_row = csv_rows[0]
            else:
                data_row = csv_rows[1]

            output_row = [patient_id] + data_row

            # Write to the CSV file and flush to save progress
            writer.writerow(output_row)
            outcsv.flush()

            # Mark as processed
            processed_ids.add(patient_id)

            # Optional: sleep to respect API rate limits
            time.sleep(0.3)

print("Extraction completed. Results saved to:", OUTPUT_CSV)













# Now open the CSV in append mode
with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as outcsv:
    writer = csv.writer(outcsv)

    # Process the TSV file line by line
    with open(TSV_INPUT, "r", newline="", encoding="utf-8") as tsvfile:
        reader = csv.reader(tsvfile, delimiter="\t")
        for row in reader:
            patient_id = row[0]
            report_text = row[1]

            # If we've already processed this patient, skip
            if patient_id in processed_ids:
                print(f"Skipping already processed ID: {patient_id}")
                continue
            
            print(f"Processing patient: {patient_id}")

            # Otherwise, call the ChatGPT API
            try:
                completion = client.chat.completions.create(
                    model=MODEL,
                    store=False,
                    seed=17,
                    messages=[
                        {
                            "role": "developer",
                            "content": [
                                {
                                    "type": "text",
                                    "text": INSTRUCTION,
                                },
                            ]
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": report_text,
                                },
                                {
                                    "type": "text",
                                    "text": PROMPT,
                                },
                            ]
                        }
                    ],
                )
            except Exception as e:
                print(f"Error processing patient {patient_id}: {e}")
                continue

            # Retrieve the answer
            answer = completion.choices[0].message.content.strip()

            # Parse the model's CSV output
            csv_lines = answer.split("\n")
            csv_rows = list(csv.reader(csv_lines))
            if len(csv_rows) == 1:
                data_row = csv_rows[0]
            else:
                data_row = csv_rows[1]

            output_row = [patient_id] + data_row

            # Write to the CSV
            writer.writerow(output_row)
            # don't buffer the CSV but write each line
            outcsv.flush()

            # Mark as processed
            processed_ids.add(patient_id)

            # Optional: sleep to respect API rate limits
            time.sleep(10)

print("Extraction completed. Results saved to:", OUTPUT_CSV)