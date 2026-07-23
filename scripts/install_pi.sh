#!/usr/bin/env bash
set -Eeuo pipefail

ORCA_VERSION="2.4.2"
ORCA_ASSET="OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage"
ORCA_URL="https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v${ORCA_VERSION}/${ORCA_ASSET}"
ORCA_SHA256="e1a07275a25f176626c55a5df39e91bc4476d8c28ee4a3192ff758e29dd5c3ba"

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="${APP_USER:-${SUDO_USER:-$(id -un)}}"
ORCA_ROOT="/opt/bambubabu/orca"
ORCA_VERSION_DIR="${ORCA_ROOT}/${ORCA_VERSION}"
RUNTIME_HOME="/var/lib/bambubabu"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This installer is pinned to the OrcaSlicer ARM64 asset; detected $(uname -m)." >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo "Cannot verify the operating system; /etc/os-release is missing." >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "This installer supports Ubuntu 24.04 ARM64; detected ${ID:-unknown} ${VERSION_ID:-unknown}." >&2
  exit 1
fi
if [[ ! -f "${APP_DIR}/requirements.lock" || ! -f "${APP_DIR}/backend/main.py" ]]; then
  echo "APP_DIR is not a BambuBabu checkout: ${APP_DIR}" >&2
  exit 1
fi
if systemctl is-active --quiet bambubabu.service 2>/dev/null; then
  echo "Stop bambubabu.service before replacing its runtime or dependencies." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl openssl python3 python3-pip python3-venv xvfb xauth \
  libwebkit2gtk-4.1-0

python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${python_version}" != "3.12" ]]; then
  echo "Ubuntu deployment requires Python 3.12; python3 is ${python_version}. Refusing an untested runtime." >&2
  exit 1
fi

download_path="$(mktemp /tmp/orcaslicer.XXXXXX.AppImage)"
extract_path="$(mktemp -d /tmp/orcaslicer-extract.XXXXXX)"
cleanup() {
  rm -f "${download_path}"
  rm -rf "${extract_path}"
}
trap cleanup EXIT

curl --fail --location --retry 3 --output "${download_path}" "${ORCA_URL}"
echo "${ORCA_SHA256}  ${download_path}" | sha256sum --check --status || {
  echo "OrcaSlicer SHA-256 verification failed; refusing to install." >&2
  exit 1
}
chmod 0755 "${download_path}"

(
  cd "${extract_path}"
  "${download_path}" --appimage-extract >/dev/null
)
test -d "${extract_path}/squashfs-root/resources/profiles/BBL"
test -x "${extract_path}/squashfs-root/AppRun"

sudo install -d -m 0755 "${ORCA_VERSION_DIR}"
sudo install -m 0755 "${download_path}" "${ORCA_VERSION_DIR}/OrcaSlicer.AppImage"
sudo install -d -m 0755 "${ORCA_VERSION_DIR}/appimage-root"
sudo cp -a "${extract_path}/squashfs-root/." "${ORCA_VERSION_DIR}/appimage-root/"
sudo chown -R root:root "${ORCA_VERSION_DIR}"
sudo ln -sfn "${ORCA_VERSION_DIR}/appimage-root" "${ORCA_ROOT}/appimage-root"
sudo ln -sfn "${ORCA_VERSION_DIR}/OrcaSlicer.AppImage" "${ORCA_ROOT}/OrcaSlicer.AppImage"

python3 -m venv --clear "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/python" -m pip install \
  --require-hashes --requirement "${APP_DIR}/requirements.lock"

sudo install -d -o "${APP_USER}" -g "$(id -gn "${APP_USER}")" -m 0750 \
  "${RUNTIME_HOME}" \
  "${RUNTIME_HOME}/uploads" \
  "${RUNTIME_HOME}/sliced" \
  "${RUNTIME_HOME}/logs" \
  "${RUNTIME_HOME}/backups" \
  "${RUNTIME_HOME}/certs"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  install -m 0600 "${APP_DIR}/.env.example" "${APP_DIR}/.env"
else
  chmod 0600 "${APP_DIR}/.env"
fi

service_tmp="$(mktemp /tmp/bambubabu-service.XXXXXX)"
sed \
  -e "s|@@APP_USER@@|${APP_USER}|g" \
  -e "s|@@APP_DIR@@|${APP_DIR}|g" \
  -e "s|@@RUNTIME_HOME@@|${RUNTIME_HOME}|g" \
  "${APP_DIR}/deploy/bambubabu.service" > "${service_tmp}"
sudo install -m 0644 "${service_tmp}" /etc/systemd/system/bambubabu.service
rm -f "${service_tmp}"
sudo systemctl daemon-reload

echo "BambuBabu dependencies and verified OrcaSlicer ${ORCA_VERSION} are installed."
echo "Next: rotate both printer LAN access codes, edit ${APP_DIR}/.env, then run:"
echo "  sudo systemctl enable --now bambubabu"
