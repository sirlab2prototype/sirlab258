#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$ROOT/.venv/bin"
COMMAND_DIR="$ROOT/scripts/bin"

if [[ ! -d "$VENV_BIN" ]]; then
    echo "ERROR: .venv가 없습니다."
    echo "먼저 프로젝트 루트에서 .venv를 생성하세요."
    exit 1
fi

commands=(
    capi
    capg
    capx
    caps
    omy
    act-record
)

for command in "${commands[@]}"; do
    source_path="$COMMAND_DIR/$command"

    if [[ ! -f "$source_path" ]]; then
        echo "ERROR: $source_path 가 없습니다."
        exit 1
    fi

    chmod +x "$source_path"

    # .venv/bin 기준 상대경로를 사용하므로
    # 프로젝트 폴더 전체를 이동해도 링크가 유지된다.
    ln -sfn "../../scripts/bin/$command" "$VENV_BIN/$command"

    echo "installed: $command"
done

echo
echo "Command installation complete."
echo "Activate with:"
echo "  source \"$ROOT/.venv/bin/activate\""
echo
echo "Available commands:"
echo "  capi  - Indy7"
echo "  capg  - Mark7 gripper"
echo "  capx  - Xbox + QP teleop"
echo "  caps  - All components"
echo "  omy   - ROBOTIS OMY environment"
