#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${APP_DIR}/.venv"
RECREATE=false

usage() {
  cat <<'EOF'
Usage: ./scripts/bootstrap_dev.sh [--recreate]

Create the supported Python 3.12 development environment and install test tools.
Use --recreate to replace an existing disposable .venv made with another Python.

Optional environment variable:
  PYTHON_BIN=/absolute/path/to/python3.12
EOF
}

case "${1:-}" in
  "") ;;
  --recreate) RECREATE=true ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

python_bin="${PYTHON_BIN:-}"
use_uv=false

if [[ -n "${python_bin}" ]]; then
  if [[ ! -x "${python_bin}" ]] && ! command -v "${python_bin}" >/dev/null 2>&1; then
    echo "PYTHON_BIN is not executable: ${python_bin}" >&2
    exit 1
  fi
elif command -v uv >/dev/null 2>&1; then
  use_uv=true
elif command -v python3.12 >/dev/null 2>&1; then
  python_bin="python3.12"
else
  cat >&2 <<'EOF'
Python 3.12 is required, but neither python3.12 nor uv was found.

On Ubuntu 24.04 install the distro interpreter:
  sudo apt-get update
  sudo apt-get install -y python3.12 python3.12-venv

On newer Ubuntu releases, install uv from its official documentation; uv can
install a managed Python 3.12 interpreter. Do not use Python 3.14 or set
PYO3_USE_ABI3_FORWARD_COMPATIBILITY for this tested release.
EOF
  exit 1
fi

if [[ "${use_uv}" == false ]]; then
  python_version="$("${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "${python_version}" != "3.12" ]]; then
    echo "BambuBabu requires Python 3.12; ${python_bin} is Python ${python_version}." >&2
    exit 1
  fi
fi

if [[ -x "${VENV_DIR}/bin/python" ]]; then
  existing_version="$("${VENV_DIR}/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "${existing_version}" != "3.12" && "${RECREATE}" == false ]]; then
    cat >&2 <<EOF
${VENV_DIR} uses Python ${existing_version}; Python 3.12 is required.
Re-run with --recreate to replace this disposable virtual environment:
  ./scripts/bootstrap_dev.sh --recreate
EOF
    exit 1
  fi
fi

if [[ "${use_uv}" == true ]]; then
  if [[ "${RECREATE}" == true ]]; then
    uv python install 3.12
    uv venv --clear --python 3.12 "${VENV_DIR}"
  elif [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    uv python install 3.12
    uv venv --python 3.12 "${VENV_DIR}"
  fi
  uv pip install --python "${VENV_DIR}/bin/python" --requirement "${APP_DIR}/requirements-dev.txt"
else
  if [[ "${RECREATE}" == true ]]; then
    "${python_bin}" -m venv --clear "${VENV_DIR}"
  elif [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "${python_bin}" -m venv "${VENV_DIR}"
  fi
  if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    "${VENV_DIR}/bin/python" -m ensurepip --upgrade
  fi
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
  "${VENV_DIR}/bin/python" -m pip install --requirement "${APP_DIR}/requirements-dev.txt"
fi

resolved_version="$("${VENV_DIR}/bin/python" --version 2>&1)"
echo "BambuBabu development environment ready: ${resolved_version}"
echo "Run:"
echo "  .venv/bin/python -m pytest -o addopts='' -W error"
echo "  .venv/bin/python -m ruff check backend tests"
