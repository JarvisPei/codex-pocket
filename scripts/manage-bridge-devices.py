#!/usr/bin/env python3
"""List or revoke Mobile Codex Bridge devices without printing bridge secrets."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import urllib.parse


def keychain_token(service: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-w",
            "-s",
            service,
            "-a",
            os.environ.get("USER", ""),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0 or len(result.stdout.strip()) < 32:
        raise SystemExit("Unable to read the bridge master token from Keychain.")
    return result.stdout.strip()


def request(method: str, path: str, token: str) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", 4317, timeout=5)
    try:
        connection.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
    finally:
        connection.close()
    if response.status >= 400:
        raise SystemExit(f"Bridge request failed: HTTP {response.status}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        choices=("list", "revoke"),
        default="list",
    )
    parser.add_argument("device_id", nargs="?")
    parser.add_argument("--keychain-service", default="mobile-codex-bridge")
    args = parser.parse_args()

    if args.action == "revoke" and not args.device_id:
        parser.error("revoke requires a device_id")
    if args.action == "list" and args.device_id:
        parser.error("list does not accept a device_id")

    token = keychain_token(args.keychain_service)
    if args.action == "revoke":
        device_id = urllib.parse.quote(args.device_id, safe="")
        request("DELETE", f"/api/devices/{device_id}", token)
        print(f"Revoked device {args.device_id}")
        return 0

    devices = request("GET", "/api/devices", token)["devices"]
    if not devices:
        print("No paired devices.")
        return 0
    for device in devices:
        print(
            f"{device['id']}\t{device['name']}\t"
            f"{device['createdAt']}\t{device['userAgent']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
