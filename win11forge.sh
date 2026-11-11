#!/usr/bin/env bash
# Win11Forge launcher for Linux. Use sudo for Windows To Go output.
exec python3 "$(dirname "$(readlink -f "$0")")/linux/win11forge.py" "$@"
