# TCGA - Pathology Report Parsing with LLM
## Overview
This project automates the processing of pathology reports from The Cancer Genome Atlas (TCGA) BRCA dataset  by combining three primary functions:

1. Downloading Reports: Uses a Bash script to download pathology reports via gdc-client based on a provided manifest.

2. OCR Processing: Utilizes the Google Cloud Vision API to perform Optical Character Recognition (OCR) on the downloaded reports, extracting text from PDFs.

3. Tumor Grade Extraction: Employs a Large Language Model (LLM) (OpenAI o3-mini) to parse the OCR output and extract tumor grade information.


## File Descriptions
00-download_pathology_reports.sh
A Bash script that downloads pathology reports using gdc-client from a manifest file.

01-OCR_with_Google_Vision_API.py
A Python script that applies the Google Cloud Vision API to perform OCR on the downloaded pathology reports, converting images or PDFs into machine-readable text.
Note: The OCR provided in the original reports is suboptimal.

02-Extract_tumor_grade_with_LLM-o3_mini.py
A Python script that processes the extracted text with a Large Language Model (LLM) to identify and extract the tumor grade from the report content.

## Environment Setup

Create the Conda Environment:
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
