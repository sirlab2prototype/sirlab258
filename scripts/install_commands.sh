#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/.." && pwd)"
DEST="$HOME/.local/bin"
REPLACE=false
case "${1:-}" in
  --replace) REPLACE=true ;;
  '') ;;
  *) echo 'Usage: install_commands.sh [--replace] (replace existing symlinks only)' >&2; exit 2 ;;
esac
(( $# <= 1 )) || exit 2
# Preflight all destinations before changing any links. Never overwrite user files.
for command in indy omy act camera cam1 cam2; do
  source_path="$ROOT/scripts/bin/$command"
  [[ -x "$source_path" ]] || { echo "Not executable: $source_path" >&2; exit 1; }
  target="$DEST/$command"
  if [[ -e "$target" || -L "$target" ]]; then
    [[ -L "$target" && ( "$(readlink -f -- "$target")" == "$source_path" || "$REPLACE" == true ) ]] || {
      echo "Refusing existing unrelated command: $target" >&2; exit 1;
    }
  fi
done
mkdir -p "$DEST"
for command in indy omy act camera cam1 cam2; do
  ln -sfn "$ROOT/scripts/bin/$command" "$DEST/$command"
  echo "installed: $DEST/$command"
done
# Retire only links proven to be from the old team installer.
for command in capi capg capx caps omy act-record; do
  target="$ROOT/.venv/bin/$command"
  if [[ -L "$target" && "$(readlink -- "$target")" == "../../scripts/bin/$command" ]]; then
    unlink "$target"
    echo "retired old link: $target"
  fi
done
echo 'Commands: indy, omy, act, camera, cam1, cam2. Ensure $HOME/.local/bin is on PATH.'
echo 'After relocating a checkout, run this installer with --replace to update its symlinks.'
echo 'Existing shells: unset -f indy omy; hash -r'
