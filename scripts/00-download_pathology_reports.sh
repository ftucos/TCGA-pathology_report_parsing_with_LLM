#!/bin/env bash
source ~/.initialize_conda
set ue -o pipefail

WD="/Users/tucos/Downloads/TCGA-pathology_report_parsing_with_LLM/"
cd $WD

mkdir -p $WD/data/pathology_report
cd $WD/data/pathology_report
gdc-client download -m  $WD/data/pathology_report-manifest.txt