set ue -o pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BASE_DEST="${SCRIPT_DIR}/../"

mkdir -p $BASE_DEST/data/pathology_report
cd $BASE_DEST/data/pathology_report
gdc-client download -m  $BASE_DEST/data/pathology_report-manifest.txt