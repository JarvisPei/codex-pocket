import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from local_hotspot_proxy import HotspotProxyHandler, is_private_ipv4


class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class HotspotProxyHandlerTest(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever,
            daemon=True,
        )
        self.upstream_thread.start()
        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), HotspotProxyHandler)
        self.proxy.upstream = ("127.0.0.1", self.upstream.server_address[1])
        self.proxy.allowed_hosts = {"192.168.13.254", "codex-pocket.local"}
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.proxy_thread.join(timeout=2)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)

    def request(self, host):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.proxy.server_address[1],
            timeout=2,
        )
        connection.request(
            "GET",
            "/health?probe=1",
            headers={"Host": host, "Authorization": "Bearer device-secret"},
        )
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_forwards_authorized_request_to_loopback_bridge(self):
        status, body = self.request("192.168.13.254:4318")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["path"], "/health?probe=1")
        self.assertEqual(payload["authorization"], "Bearer device-secret")

    def test_rejects_unexpected_host_header(self):
        status, body = self.request("attacker.example")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_host")


class HotspotAddressValidationTest(unittest.TestCase):
    def test_accepts_only_rfc1918_ipv4(self):
        self.assertTrue(is_private_ipv4("192.168.13.254"))
        self.assertTrue(is_private_ipv4("10.0.0.2"))
        self.assertFalse(is_private_ipv4("100.74.176.30"))
        self.assertFalse(is_private_ipv4("8.8.8.8"))


if __name__ == "__main__":
    unittest.main()
