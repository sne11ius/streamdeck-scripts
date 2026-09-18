#!/usr/bin/env bash
# Python owns setup, the private environment, and only its disposable processes.
set -euo pipefail
exec /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/konsole-session-integration.py" "$@"
