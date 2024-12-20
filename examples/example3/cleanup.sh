#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

rm -f ./*.log
rm -f ./outputs/file-created-by-program3.log
rm -f ./outputs/logs/shepherd.log
rm -f ./state_transition_times.json

rmdir ./outputs/logs/ || true
rmdir ./outputs/ || true
