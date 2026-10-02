#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROS_DISTRO="jazzy"
ROS_SETUP="/opt/ros/$ROS_DISTRO/setup.bash"
VENV="$ROOT/.venv"
WS="$ROOT/ros2_ws"

echo "=== Capstone bootstrap ==="
echo "Project: $ROOT"
echo

# --------------------------------------------------
# 1. System prerequisite checks
# --------------------------------------------------

if [[ ! -f "$ROS_SETUP" ]]; then
    echo "ERROR: ROS 2 Jazzy not found:"
    echo "  $ROS_SETUP"
    echo
    echo "Install ROS 2 Jazzy first, then run this script again."
    exit 1
fi

for cmd in python3 git colcon; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: required command not found: $cmd"
        exit 1
    fi
done

if [[ ! -f "$ROOT/requirements.txt" ]]; then
    echo "ERROR: requirements.txt not found"
    exit 1
fi

if [[ ! -d "$WS/src" ]]; then
    echo "ERROR: ROS workspace not found:"
    echo "  $WS"
    exit 1
fi

echo "[1/5] System prerequisites OK"

# --------------------------------------------------
# 2. Git submodules
# --------------------------------------------------

cd "$ROOT"

git submodule update --init --recursive

echo "[2/5] Git submodules ready"

# --------------------------------------------------
# 3. Project-local Python environment
# --------------------------------------------------

if [[ ! -d "$VENV" ]]; then
    echo "Creating .venv..."
    python3 -m venv --system-site-packages "$VENV"
else
    echo ".venv already exists; reusing it"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install -r "$ROOT/requirements.txt"

echo "[3/5] Python environment ready"

# --------------------------------------------------
# 4. ROS 2 build
# --------------------------------------------------

# ROS 2 setup scripts may reference optional unset variables.
# Temporarily disable nounset while sourcing them.
set +u
# shellcheck disable=SC1090
source "$ROS_SETUP"
set -u

cd "$WS"

# Do NOT use --symlink-install.
# The pinned official indy-ros2 currently has executable/permission
# behavior that makes the normal install build more reliable here.
colcon build

echo "[4/5] ROS workspace built"

# --------------------------------------------------
# 5. Project commands
# --------------------------------------------------

cd "$ROOT"

"$ROOT/scripts/install_commands.sh"

echo "[5/5] Project commands installed"

echo
echo "========================================"
echo "Bootstrap complete."
echo
echo "Use:"
echo "  cd \"$ROOT\""
echo "  source .venv/bin/activate"
echo
echo "Commands:"
echo "  capi   Indy7"
echo "  capg   Mark7 gripper"
echo "  capx   Xbox + QP teleop"
echo "  caps   Full manual teleop setup"
echo "========================================"
