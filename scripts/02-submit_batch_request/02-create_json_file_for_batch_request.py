import os
import re
import csv
import json
import time

########################################
# Adapted Code to Generate a JSON Batch Request File
#
# This script reads files from a directory and then
# creates a JSON lines file where each line is one request to the OpenAI ChatGPT.
# The resulting file can be sent to a batch processing service.
########################################

# Directory containing your pathology report text files
INPUT_DIR = "./processed/pathology_report_transcript/"  # Update as needed

# JSON lines output file
OUTPUT_JSON = "./processed/batch_requests/chatgpt_batch.jsonl"  # Update as needed

# Ensure the output directory exists
os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)

# The model you want to use for these requests
MODEL = "o3-mini-2025-01-31"

# System prompt or general instruction for ChatGPT

INSTRUCTION = """You are a specialized AI tool with background knowledge in surgical pathology and you help physicians extract relevant information from pathology reports of breast cancer patients.
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
Infiltrating ductal carcinoma NOS,G1,Multiple lesions; grade inferred from Notthingam score = 4/9"""
# Prompt
PROMPT = """
Please extract the Tumor Histology and Tumor Grade from the provided transcription of the pathology report of a breast cancer patient. Follow the output formatting rules strictly.
"""

# We'll store our batch requests here
requests_data = []
file_counter = 0

# Option 1: Reading from a directory of text files
# Each file is assumed to contain the entire pathology report for one case.

for filename in os.listdir(INPUT_DIR):
    # Example: proceed only if filename starts with "TCGA" and ends with ".txt"
    if filename.startswith("TCGA") and filename.endswith(".txt"):
        # Extract a patient ID from the filename, e.g. "TCGA-XX-XXXX"
        patient_id = re.search(r"(TCGA-[A-Z0-9]{2}-[A-Z0-9]+)", filename).group(1)
        
        file_path = os.path.join(INPUT_DIR, filename)

        try:
            with open(file_path, "r", encoding="utf-8") as infile:
                report_text = infile.read()
        except Exception as e:
            print(f"Error reading file {filename}: {e}")
            continue

        # Build the message array for ChatGPT
        messages = [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": report_text},
            {"role": "user", "content": PROMPT}
        ]

        # Assign a custom_id that references the patient
        file_counter += 1
        custom_id = f"{patient_id}-R{file_counter}"

        # Construct the JSON object for this single request
        request_obj = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "store": False,
                "seed": 17,
                "messages": messages,
                "max_tokens": 1000
            }
        }

        requests_data.append(request_obj)

# Finally, write the requests to a JSON lines file
with open(OUTPUT_JSON, "w", encoding="utf-8") as outjson:
    length = len(requests_data)
    for i, request_item in enumerate(requests_data):
        outjson.write(json.dumps(request_item))
        if i < length - 1:
            outjson.write("\n")

print(f"Created JSON batch file with {len(requests_data)} requests: {OUTPUT_JSON}")
