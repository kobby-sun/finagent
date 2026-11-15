#!/usr/bin/env bash
# Delete local runtime state (runs, approvals, audit, index). Mock data and corpus are untouched.
set -euo pipefail
cd "$(dirname "$0")/../.."
read -r -p "Delete ./data (runs, approvals, audit, index)? [y/N] " a; [ "$a" = "y" ] && rm -rf data && echo "deleted"
