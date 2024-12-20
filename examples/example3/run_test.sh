#!/bin/bash
set -euo pipefail

echo "Starting test ..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
echo "Script directory: ${SCRIPT_DIR}"
shepherd --config "${SCRIPT_DIR}/shepherd-config.yaml" \
    --work-dir "${SCRIPT_DIR}" \
    --run-dir "${SCRIPT_DIR}/outputs"

echo "Completed test"
