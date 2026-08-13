#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_path="$project_root/.venv/bin/python"

if [ ! -x "$python_path" ]; then
    echo "CutFrame's .venv is missing. Follow the macOS/Linux setup steps in README.md first." >&2
    exit 1
fi

exec "$python_path" "$project_root/app.py"
