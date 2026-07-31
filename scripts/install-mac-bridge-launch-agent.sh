#!/bin/zsh

set -euo pipefail

agent_label="com.local.mobile-codex-bridge"
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
user_apps_dir="${user_home_path}/Applications"
agent_path="${launch_agents_dir}/${agent_label}.plist"
template_path="${repo_root}/launchd/${agent_label}.plist"
launch_domain="gui/${current_uid}"
helper_app="${user_apps_dir}/MobileCodexBridgeHelper.app"
helper_contents="${helper_app}/Contents"
helper_binary="${helper_contents}/MacOS/mobile-codex-ax"
helper_info="${helper_contents}/Info.plist"
helper_source="${repo_root}/scripts/codex-ax.swift"
helper_source_info="${repo_root}/mac-helper/Info.plist"
helper_resources="${helper_contents}/Resources"
helper_stamp="${helper_resources}/source.sha256"
compatible_sdk="/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk"
expected_helper_stamp="$(
  /usr/bin/shasum -a 256 "${helper_source}" "${helper_source_info}" |
    /usr/bin/shasum -a 256 |
    /usr/bin/awk '{print $1}'
)"

/bin/mkdir -p \
  "${launch_agents_dir}" \
  "${user_log_dir}" \
  "${helper_contents}/MacOS" \
  "${helper_resources}"

reuse_helper=false
if [[ -x "${helper_binary}" && -f "${helper_info}" ]]; then
  if [[ -f "${helper_stamp}" ]] && \
    [[ "$(<"${helper_stamp}")" == "${expected_helper_stamp}" ]]; then
    reuse_helper=true
  elif /usr/bin/cmp -s "${helper_source_info}" "${helper_info}" && \
    [[ "${helper_binary}" -nt "${helper_source}" ]]; then
    # Upgrade an installation made before source stamps were introduced without
    # rebuilding its already-authorized, matching helper.
    reuse_helper=true
  fi
fi

helper_needs_sign=false
if [[ "${reuse_helper}" != true ]]; then
  helper_build="$(/usr/bin/mktemp /private/tmp/mobile-codex-ax.XXXXXX)"
  trap '/bin/rm -f "${helper_build}"' EXIT
  if [[ -d "${compatible_sdk}" ]]; then
    CLANG_MODULE_CACHE_PATH=/private/tmp/mobile-codex-clang-cache \
      /usr/bin/xcrun swiftc \
      -sdk "${compatible_sdk}" \
      -target "$(/usr/bin/uname -m)-apple-macosx15.4" \
      "${helper_source}" \
      -o "${helper_build}"
  else
    CLANG_MODULE_CACHE_PATH=/private/tmp/mobile-codex-clang-cache \
      /usr/bin/xcrun swiftc "${helper_source}" -o "${helper_build}"
  fi
  /usr/bin/install -m 0755 "${helper_build}" "${helper_binary}"
  /bin/rm -f "${helper_build}"
  trap - EXIT
  /usr/bin/install -m 0644 "${helper_source_info}" "${helper_info}"
  helper_needs_sign=true
fi
if [[ ! -f "${helper_stamp}" ]] || \
  [[ "$(<"${helper_stamp}")" != "${expected_helper_stamp}" ]]; then
  /usr/bin/printf '%s\n' "${expected_helper_stamp}" > "${helper_stamp}"
  helper_needs_sign=true
fi
if ! /usr/bin/codesign --verify --deep --strict "${helper_app}" >/dev/null 2>&1; then
  helper_needs_sign=true
fi
if [[ "${helper_needs_sign}" == true ]]; then
  # The source stamp is a sealed resource, so it must exist before signing.
  # Writing it after codesign makes the Accessibility identity unusable even
  # though System Settings still shows the helper as enabled.
  /usr/bin/codesign --force --sign - --timestamp=none "${helper_app}"
fi

/usr/bin/install -m 0644 "${template_path}" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:1 ${repo_root}/mac_bridge.py" "${agent_path}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:7 ${helper_binary}" "${agent_path}"
/usr/bin/plutil -replace WorkingDirectory \
  -string "${repo_root}" "${agent_path}"
/usr/bin/plutil -replace StandardOutPath \
  -string "${user_log_dir}/mobile-codex-bridge.log" "${agent_path}"
/usr/bin/plutil -replace StandardErrorPath \
  -string "${user_log_dir}/mobile-codex-bridge.error.log" "${agent_path}"

if /bin/launchctl print "${launch_domain}/${agent_label}" >/dev/null 2>&1; then
  /bin/launchctl bootout "${launch_domain}/${agent_label}"
fi
if ! /bin/launchctl bootstrap "${launch_domain}" "${agent_path}"; then
  # launchd can briefly retain the old label after bootout. A bounded retry
  # prevents an otherwise valid update from leaving the bridge offline.
  /bin/sleep 1
  /bin/launchctl bootstrap "${launch_domain}" "${agent_path}"
fi
/bin/launchctl enable "${launch_domain}/${agent_label}"

echo "Installed and started ${agent_label}"
