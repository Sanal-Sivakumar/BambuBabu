#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <printer-ip> <mqtt-certificate-output.pem>" >&2
  exit 2
fi

printer_ip="$1"
certificate_output="$2"
if [[ ! "${printer_ip}" =~ ^[0-9A-Fa-f:.]+$ ]]; then
  echo "Printer address must be a literal IPv4 or IPv6 LAN address." >&2
  exit 2
fi

connect_host="${printer_ip}"
if [[ "${printer_ip}" == *:* ]]; then
  connect_host="[${printer_ip}]"
fi
if [[ -e "${certificate_output}" || -L "${certificate_output}" ]]; then
  echo "Refusing to overwrite existing certificate: ${certificate_output}" >&2
  exit 1
fi

certificate_parent="$(dirname "${certificate_output}")"
install -d -m 0750 "${certificate_parent}"
certificate_temp="$(mktemp "${certificate_parent}/.mqtt-cert.XXXXXX")"
cleanup() {
  rm -f "${certificate_temp}"
}
trap cleanup EXIT

# Run this only on a trusted, isolated LAN immediately after rotating the printer
# access code. The captured identity is then enforced on every MQTT connection.
timeout 20 openssl s_client -connect "${connect_host}:8883" -showcerts </dev/null 2>/dev/null \
  | openssl x509 -outform PEM >"${certificate_temp}"
openssl x509 -in "${certificate_temp}" -noout -subject -issuer -fingerprint -sha256

ftps_pin="$(
  timeout 20 openssl s_client -connect "${connect_host}:990" -showcerts </dev/null 2>/dev/null \
    | openssl x509 -pubkey -noout \
    | openssl pkey -pubin -outform DER 2>/dev/null \
    | openssl dgst -sha256 -binary \
    | openssl base64 -A
)"

if [[ ! "${ftps_pin}" =~ ^[A-Za-z0-9+/]{43}=$ ]]; then
  echo "Could not derive the printer FTPS public-key pin." >&2
  exit 1
fi

install -m 0640 "${certificate_temp}" "${certificate_output}"
echo "MQTT certificate saved to: ${certificate_output}"
echo "FTPS pin for .env: sha256//${ftps_pin}"
