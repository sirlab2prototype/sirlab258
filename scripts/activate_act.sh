#!/usr/bin/env bash

_ACT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$_ACT_ROOT/.venv-act/bin/activate" ]]; then
    echo "ERROR: $_ACT_ROOT/.venv-act is missing." >&2
    return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1091
source "$_ACT_ROOT/.venv-act/bin/activate"
export PYTHONPATH="$_ACT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="${HF_HOME:-$_ACT_ROOT/.hf_cache}"
if [[ -f "$_ACT_ROOT/ai/record/config/home_capture.json" ]]; then
    export SIRLAB258_HOME_PROFILE="$_ACT_ROOT/ai/record/config/home_capture.json"
fi

echo "ACT environment: $_ACT_ROOT/.venv-act"
echo "LeRobot source:  $_ACT_ROOT/ai/lerobot_source/lerobot"
