#!/usr/bin/env python

# create the processor on cloud console
# login on gcloud: gcloud auth application-default login
# expected cost ~ $2.5

import os
from google.api_core.client_options import ClientOptions
from google.cloud import documentai_v1

# Initialize directory paths
FOLDER_PATH = "./data/pathology_report/"
OUT_DIR = "./processed/pathology_report_transcript/"

# Ensure output directory exists
os.makedirs(OUT_DIR, exist_ok=True)

project_id = "neat-bank-456806-u4"

# Processor ID as hexadecimal characters.
# Not to be confused with the Processor Display Name.
processor_id = "1e83c869c75aaca"

# Processor location. For example: "us" or "eu".
location = "eu"

# Set API endpoint for the selected location.
opts = ClientOptions(
    api_endpoint=f"{location}-documentai.googleapis.com"
)

# Initialize Document AI client.
client = documentai_v1.DocumentProcessorServiceClient(
    client_options=opts
)

# Get the fully qualified processor path.
full_processor_name = client.processor_path(
    project_id,
    location,
    processor_id
)

# Get processor reference.
request = documentai_v1.GetProcessorRequest(
    name=full_processor_name
)

processor = client.get_processor(request=request)

print(f"Processor Name: {processor.name}")


# Walk through each subfolder and look for PDF files
for root, dirs, files in os.walk(FOLDER_PATH):
    for file_name in files:

        if not file_name.lower().endswith(".pdf"):
            continue

        pdf_path = os.path.join(root, file_name)
        pdf_base_name = os.path.splitext(file_name)[0]

        output_path = os.path.join(
            OUT_DIR,
            pdf_base_name + ".txt"
        )

        # Skip files that have already been processed
        if os.path.exists(output_path):
            print(f"⏩ Skipping already processed: {file_name}")
            continue

        print(f"Processing: {file_name}")

        # Read the PDF into memory
        with open(pdf_path, "rb") as pdf_file:
            pdf_content = pdf_file.read()

        raw_document = documentai_v1.RawDocument(
            content=pdf_content,
            mime_type="application/pdf",
        )

        # Send request to Google Document AI
        request = documentai_v1.ProcessRequest(
            name=processor.name,
            raw_document=raw_document
        )

        result = client.process_document(request=request)
        document = result.document

        # Save OCR transcript
        with open(output_path, "w", encoding="utf-8") as output_file:
            output_file.write(document.text)

        print(f"✅ Saved: {output_path}")
        