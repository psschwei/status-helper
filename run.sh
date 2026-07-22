#!/usr/bin/env bash
#
# Start the Engineering Status Assistant dev server.
#
# Wraps `uv run uvicorn ...` so you don't have to remember it, with a quick preflight
# check for the two files that are easy to forget on first run (both git-ignored, each
# with a committed *.example to copy from). Any arguments are passed through to uvicorn,
# e.g. `./run.sh --port 9000`.
set -euo pipefail

# Run from the repository root regardless of where the script is invoked from.
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "error: .env not found. Copy the template and set your GitHub token:" >&2
  echo "  cp .env.example .env" >&2
  exit 1
fi

if [[ ! -f repos.toml ]]; then
  echo "error: repos.toml not found. Copy the template and list repositories to watch:" >&2
  echo "  cp repos.toml.example repos.toml" >&2
  exit 1
fi

# --reload restarts on code changes (dev default). Extra args flow through to uvicorn.
exec uv run uvicorn status_assistant.main:app --reload "$@"
