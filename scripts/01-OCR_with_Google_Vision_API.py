# create the processor on cloud consol
# login on gcloud `gcloud auth application-default login``
# expected cost ~ 2.5$

import os
from google.api_core.client_options import ClientOptions
from google.cloud import documentai_v1

# Initialize your directory paths
FOLDER_PATH = "./data/pathology_report/"  
OUT_DIR = "./processed/pathology_report_transcript/"

# Ensure output directory exists
os.makedirs(os.path.dirname(OUT_DIR), exist_ok=True)

project_id = "neat-bank-456806-u4"

# Processor ID as hexadecimal characters.
# Not to be confused with the Processor Display Name.
processor_id = "1e83c869c75aaca"

# Processor location. For example: "us" or "eu".
location = "eu"

# Set `api_endpoint` if you use a location other than "us".
opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")

# Initialize Document AI client.
client = documentai_v1.DocumentProcessorServiceClient(client_options=opts)

# Get the Fully-qualified Processor path.
full_processor_name = client.processor_path(project_id, location, processor_id)

# Get a Processor reference.
request = documentai_v1.GetProcessorRequest(name=full_processor_name)
processor = client.get_processor(request=request)

# `processor.name` is the full resource name of the processor.
# For example: `projects/{project_id}/locations/{location}/processors/{processor_id}`
print(f"Processor Name: {processor.name}")


# Walk through each subfolder and look for PDF files
for root, dirs, files in os.walk(FOLDER_PATH):
	for file_name in files:
		if file_name.lower().endswith(".pdf"):
			pdf_path = os.path.join(root, file_name)
			pdf_base_name = os.path.splitext(file_name)[0]

			print(f"Processing: {file_name}")
			# Read the file into memory.
			with open(pdf_path, "rb") as image:
				image_content = image.read()

			# Load binary data.
			# For supported MIME types, refer to https://cloud.google.com/document-ai/docs/file-types
			raw_document = documentai_v1.RawDocument(
				content=image_content,
				mime_type="application/pdf",
			)

			# Send a request and get the processed document.
			request = documentai_v1.ProcessRequest(name=processor.name, raw_document=raw_document)
			result = client.process_document(request=request)
			document = result.document
			text = document.text 
			
			with open(OUT_DIR + pdf_base_name + ".txt", "w", encoding="utf-8") as file:
				file.write(document.text)

