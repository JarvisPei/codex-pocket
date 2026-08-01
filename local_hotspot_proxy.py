#!/usr/bin/env python3
"""TLS-only, hotspot-scoped reverse proxy for the localhost Codex Pocket bridge."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import os
import ssl
import stat
import subprocess
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional


MAX_REQUEST_BODY_BYTES = 24 * 1024 * 1024
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def is_private_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.version == 4 and any(
        address in network for network in PRIVATE_IPV4_NETWORKS
    )


def read_default_gateway() -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["/sbin/route", "-n", "get", "default"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if result.returncode != 0:
        return "", ""
    gateway = ""
    interface = ""
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        if key == "gateway":
            gateway = value.strip()
        elif key == "interface":
            interface = value.strip()
    return gateway, interface


class HotspotProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        upstream: tuple[str, int],
        allowed_hosts: set[str],
        tls_context: ssl.SSLContext,
    ) -> None:
        self.upstream = upstream
        self.allowed_hosts = allowed_hosts
        self.tls_context = tls_context
        super().__init__(address, HotspotProxyHandler)

    def get_request(self):
        raw_socket, client_address = super().get_request()
        raw_socket.settimeout(15)
        try:
            tls_socket = self.tls_context.wrap_socket(
                raw_socket,
                server_side=True,
                do_handshake_on_connect=False,
            )
        except Exception:
            raw_socket.close()
            raise
        # The handler thread performs the TLS handshake on its first read. This
        # keeps one slow mobile connection from blocking other parallel browser
        # asset requests in the main accept loop.
        return tls_socket, client_address


class HotspotProxyHandler(BaseHTTPRequestHandler):
    server: HotspotProxyServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # Keep logs metadata-only. Authorization and request bodies are never logged.
        print(f"{self.client_address[0]} {format % args}", flush=True)

    def _send_error_json(self, status: HTTPStatus, error: str) -> None:
        body = f'{{"error":"{error}"}}'.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _valid_host(self) -> bool:
        supplied = self.headers.get("Host", "")
        try:
            parsed = urllib.parse.urlsplit(f"//{supplied}")
            hostname = parsed.hostname or ""
        except ValueError:
            return False
        return hostname.lower() in self.server.allowed_hosts

    def _proxy(self) -> None:
        if not self._valid_host():
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_host")
            return
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        if transfer_encoding:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "streaming_request_not_supported",
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_content_length")
            return
        if length < 0 or length > MAX_REQUEST_BODY_BYTES:
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large")
            return
        body = self.rfile.read(length) if length else None
        forwarded_headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS
            and name.lower() not in {"host", "content-length"}
        }
        forwarded_headers["Host"] = (
            f"{self.server.upstream[0]}:{self.server.upstream[1]}"
        )
        if body is not None:
            forwarded_headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(
            self.server.upstream[0],
            self.server.upstream[1],
            timeout=30,
        )
        try:
            connection.request(self.command, self.path, body=body, headers=forwarded_headers)
            response = connection.getresponse()
            response_body = response.read()
        except (OSError, http.client.HTTPException):
            self._send_error_json(HTTPStatus.BAD_GATEWAY, "bridge_unavailable")
            return
        finally:
            connection.close()
        self.send_response(response.status, response.reason)
        for name, value in response.getheaders():
            if name.lower() in HOP_BY_HOP_HEADERS or name.lower() == "content-length":
                continue
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(response_body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    do_GET = _proxy
    do_POST = _proxy
    do_DELETE = _proxy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--expected-gateway", required=True)
    parser.add_argument("--expected-interface", default="en0")
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=4317)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not is_private_ipv4(args.listen_host):
        raise SystemExit("The hotspot listener must use an RFC1918 IPv4 address.")
    if not is_private_ipv4(args.expected_gateway):
        raise SystemExit("The expected hotspot gateway must use an RFC1918 IPv4 address.")
    try:
        if not ipaddress.ip_address(args.upstream_host).is_loopback:
            raise SystemExit("The hotspot proxy upstream must remain on loopback.")
    except ValueError as error:
        raise SystemExit("The hotspot proxy upstream must be a loopback IP.") from error
    if not (1 <= args.port <= 65535 and 1 <= args.upstream_port <= 65535):
        raise SystemExit("Invalid TCP port.")
    for path, private in ((args.certificate, False), (args.private_key, True)):
        resolved = path.expanduser().resolve()
        try:
            metadata = resolved.stat()
        except OSError as error:
            raise SystemExit(f"Unable to read TLS material: {error}") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemExit("TLS material must be regular files.")
        if private and metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SystemExit("The TLS private key must be user-only.")


def tls_context(args: argparse.Namespace) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        args.certificate.expanduser().resolve(),
        args.private_key.expanduser().resolve(),
    )
    return context


def hotspot_matches(args: argparse.Namespace) -> bool:
    gateway, interface = read_default_gateway()
    return gateway == args.expected_gateway and interface == args.expected_interface


def serve_when_hotspot_matches(args: argparse.Namespace) -> None:
    while True:
        if not hotspot_matches(args):
            time.sleep(3)
            continue
        try:
            server = HotspotProxyServer(
                (args.listen_host, args.port),
                (args.upstream_host, args.upstream_port),
                {args.listen_host.lower(), "codex-pocket.local"},
                tls_context(args),
            )
        except OSError as error:
            print(f"hotspot proxy waiting for {args.listen_host}: {error}", flush=True)
            time.sleep(3)
            continue
        server.timeout = 2
        print(
            f"hotspot proxy listening on https://{args.listen_host}:{args.port}",
            flush=True,
        )
        try:
            while hotspot_matches(args):
                server.handle_request()
        finally:
            server.server_close()
            print("hotspot proxy paused because the network changed", flush=True)


def main() -> int:
    args = parse_args()
    validate_args(args)
    try:
        serve_when_hotspot_matches(args)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
