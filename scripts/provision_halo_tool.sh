#!/usr/bin/env bash
set -euo pipefail

HALO_ENGINE_VERSION="${HALO_ENGINE_VERSION:-0.1.5}"
TOOL_ROOT="${AUTORESEARCH_HALO_TOOL_ROOT:-/opt/autoresearch-tools/halo}"
VENV_DIR="${TOOL_ROOT}/venv"
LOCK_DIR="${TOOL_ROOT}/.install.lock"

mkdir -p "${TOOL_ROOT}"

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "HALO tool provisioning is already running: ${LOCK_DIR}" >&2
  exit 75
fi

cleanup() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install "halo-engine==${HALO_ENGINE_VERSION}"

HALO_BIN="${VENV_DIR}/bin/halo"
if [[ ! -x "${HALO_BIN}" ]]; then
  echo "HALO binary was not created or is not executable: ${HALO_BIN}" >&2
  exit 1
fi

echo "HALO CLI provisioned at ${HALO_BIN}"
echo "export AUTORESEARCH_HALO_BINARY=${HALO_BIN}"
