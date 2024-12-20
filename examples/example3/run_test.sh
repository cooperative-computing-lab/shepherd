#!/bin/bash
set -euo pipefail

echo "Starting test ..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
echo "Script directory: ${SCRIPT_DIR}"
shepherd --run-dir "${SCRIPT_DIR}" --work-dir "${SCRIPT_DIR}" --config "${SCRIPT_DIR}/shepherd-config.yaml"

echo "Completed test"
