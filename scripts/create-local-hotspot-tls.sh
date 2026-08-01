#!/bin/zsh

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <hotspot-ip> [output-directory]" >&2
  exit 2
fi

hotspot_ip="$1"
output_dir="${2:-${HOME}/Library/Application Support/MobileCodexBridge/local-hotspot-tls}"

if ! /usr/bin/python3 -c \
  'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); raise SystemExit(0 if a.version == 4 and a.is_private else 1)' \
  "${hotspot_ip}"; then
  echo "Hotspot IP must be a private IPv4 address." >&2
  exit 2
fi

/bin/mkdir -p "${output_dir}"
/bin/chmod 700 "${output_dir}"

ca_key="${output_dir}/local-ca.key"
ca_cert="${output_dir}/local-ca.crt"
ca_der="${output_dir}/local-ca.cer"
server_key="${output_dir}/server.key"
server_csr="${output_dir}/server.csr"
server_cert="${output_dir}/server.crt"
metadata="${output_dir}/metadata.json"
ca_config="$(/usr/bin/mktemp /private/tmp/codex-pocket-ca.XXXXXX)"
server_config="$(/usr/bin/mktemp /private/tmp/codex-pocket-server.XXXXXX)"
trap '/bin/rm -f "${ca_config}" "${server_config}" "${server_csr}"' EXIT

if [[ -f "${metadata}" ]]; then
  existing_ip="$(
    /usr/bin/python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("listenHost", ""))' \
      "${metadata}" 2>/dev/null || true
  )"
  if [[ -n "${existing_ip}" && "${existing_ip}" != "${hotspot_ip}" ]]; then
    echo "Existing local CA is constrained to ${existing_ip}; remove the TLS directory before changing hotspot IP." >&2
    exit 1
  fi
fi

/usr/bin/printf '%s\n' \
  '[req]' \
  'prompt = no' \
  'distinguished_name = dn' \
  'x509_extensions = v3_ca' \
  '[dn]' \
  'CN = Codex Pocket Local Hotspot CA' \
  '[v3_ca]' \
  'basicConstraints = critical, CA:TRUE, pathlen:0' \
  'keyUsage = critical, keyCertSign, cRLSign' \
  'subjectKeyIdentifier = hash' \
  'authorityKeyIdentifier = keyid:always' \
  "nameConstraints = critical, permitted;IP:${hotspot_ip}/255.255.255.255, permitted;DNS:codex-pocket.local" \
  > "${ca_config}"

/usr/bin/printf '%s\n' \
  '[req]' \
  'prompt = no' \
  'distinguished_name = dn' \
  'req_extensions = server_ext' \
  '[dn]' \
  'CN = codex-pocket.local' \
  '[server_ext]' \
  'basicConstraints = critical, CA:FALSE' \
  'keyUsage = critical, digitalSignature, keyEncipherment' \
  'extendedKeyUsage = serverAuth' \
  "subjectAltName = DNS:codex-pocket.local, IP:${hotspot_ip}" \
  'subjectKeyIdentifier = hash' \
  > "${server_config}"

if [[ ! -f "${ca_key}" || ! -f "${ca_cert}" ]]; then
  /usr/bin/openssl genrsa -out "${ca_key}" 3072
  /usr/bin/openssl req -x509 -new -sha256 \
    -key "${ca_key}" \
    -days 3650 \
    -config "${ca_config}" \
    -out "${ca_cert}"
fi

/usr/bin/openssl genrsa -out "${server_key}" 2048
/usr/bin/openssl req -new -sha256 \
  -key "${server_key}" \
  -config "${server_config}" \
  -out "${server_csr}"
/usr/bin/openssl x509 -req -sha256 \
  -in "${server_csr}" \
  -CA "${ca_cert}" \
  -CAkey "${ca_key}" \
  -CAcreateserial \
  -days 397 \
  -extfile "${server_config}" \
  -extensions server_ext \
  -out "${server_cert}"
/usr/bin/openssl x509 -in "${ca_cert}" -outform DER -out "${ca_der}"
/usr/bin/openssl verify -CAfile "${ca_cert}" "${server_cert}"

fingerprint="$(
  /usr/bin/openssl x509 -in "${ca_cert}" -noout -fingerprint -sha256 |
    /usr/bin/awk -F= '{print $2}'
)"
/usr/bin/printf '{"listenHost":"%s","dnsName":"codex-pocket.local","caSha256":"%s"}\n' \
  "${hotspot_ip}" "${fingerprint}" > "${metadata}"

/bin/chmod 600 "${ca_key}" "${server_key}" "${metadata}"
/bin/chmod 644 "${ca_cert}" "${ca_der}" "${server_cert}"

echo "Created hotspot TLS material in ${output_dir}"
echo "CA fingerprint: ${fingerprint}"
