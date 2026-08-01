#!/bin/zsh

set -euo pipefail

hotspot_port="${1:-4318}"
if ! [[ "${hotspot_port}" =~ '^[0-9]+$' ]] || \
  (( hotspot_port < 1024 || hotspot_port > 65535 )); then
  echo "Hotspot port must be between 1024 and 65535." >&2
  exit 2
fi

agent_label="com.local.codex-pocket-hotspot"
script_dir="${0:A:h}"
repo_root="${script_dir:h}"
current_user="$(/usr/bin/id -un)"
current_uid="$(/usr/bin/id -u)"
user_home_path="$(
  /usr/bin/dscl . -read "/Users/${current_user}" NFSHomeDirectory |
    /usr/bin/awk '{print $2}'
)"
launch_agents_dir="${user_home_path}/Library/LaunchAgents"
user_log_dir="${user_home_path}/Library/Logs"
state_dir="${user_home_path}/Library/Application Support/MobileCodexBridge"
tls_dir="${state_dir}/local-hotspot-tls"
config_path="${state_dir}/local-hotspot.json"
agent_path="${launch_agents_dir}/${agent_label}.plist"
template_path="${repo_root}/launchd/${agent_label}.plist"
launch_domain="gui/${current_uid}"

route_output="$(/sbin/route -n get default)"
gateway="$(/usr/bin/awk '/gateway:/{print $2; exit}' <<< "${route_output}")"
interface="$(/usr/bin/awk '/interface:/{print $2; exit}' <<< "${route_output}")"
listen_host="$(/usr/sbin/ipconfig getifaddr "${interface}")"

if [[ -z "${gateway}" || -z "${interface}" || -z "${listen_host}" ]]; then
  echo "Unable to identify the active hotspot network." >&2
  exit 1
fi
if ! /usr/bin/python3 -c \
  'import ipaddress,sys; raise SystemExit(0 if all(ipaddress.ip_address(v).is_private for v in sys.argv[1:]) else 1)' \
  "${gateway}" "${listen_host}"; then
  echo "The active network is not using private hotspot addresses." >&2
  exit 1
fi

"${repo_root}/scripts/create-local-hotspot-tls.sh" "${listen_host}" "${tls_dir}"

/bin/mkdir -p "${launch_agents_dir}" "${user_log_dir}" "${state_dir}"
/bin/chmod 700 "${state_dir}"
/usr/bin/install -m 0644 "${template_path}" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:1 ${repo_root}/local_hotspot_proxy.py" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:3 ${listen_host}" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:5 ${hotspot_port}" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:7 ${gateway}" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:9 ${interface}" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:11 ${tls_dir}/server.crt" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:13 ${tls_dir}/server.key" "${agent_path}"
/usr/bin/plutil -replace WorkingDirectory -string "${repo_root}" "${agent_path}"
/usr/bin/plutil -replace StandardOutPath \
  -string "${user_log_dir}/codex-pocket-hotspot.log" "${agent_path}"
/usr/bin/plutil -replace StandardErrorPath \
  -string "${user_log_dir}/codex-pocket-hotspot.error.log" "${agent_path}"

ca_fingerprint="$(
  /usr/bin/openssl x509 -in "${tls_dir}/local-ca.crt" -noout -fingerprint -sha256 |
    /usr/bin/awk -F= '{print $2}'
)"
/usr/bin/printf \
  '{"enabled":true,"listenHost":"%s","port":%s,"expectedGateway":"%s","expectedInterface":"%s","url":"https://%s:%s/","caSha256":"%s"}\n' \
  "${listen_host}" "${hotspot_port}" "${gateway}" "${interface}" \
  "${listen_host}" "${hotspot_port}" "${ca_fingerprint}" \
  > "${config_path}"
/bin/chmod 600 "${config_path}"

if /bin/launchctl print "${launch_domain}/${agent_label}" >/dev/null 2>&1; then
  /bin/launchctl bootout "${launch_domain}/${agent_label}"
fi
if ! /bin/launchctl bootstrap "${launch_domain}" "${agent_path}"; then
  /bin/sleep 1
  /bin/launchctl bootstrap "${launch_domain}" "${agent_path}"
fi
/bin/launchctl enable "${launch_domain}/${agent_label}"

echo "Installed hotspot proxy at https://${listen_host}:${hotspot_port}/"
echo "Trusted CA: ${tls_dir}/local-ca.cer"
