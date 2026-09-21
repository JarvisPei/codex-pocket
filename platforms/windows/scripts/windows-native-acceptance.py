"""Explicit real-model acceptance test. Does not run in the unit-test suite."""
from pathlib import Path
import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from platforms.windows.bridge import make_server, windows_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--confirm-send", action="store_true", required=True)
    parser.add_argument("--request-id", default=None)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows")
    token = windows_token()
    server = make_server(args.codex_binary, token, 0, native_text_send=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    payload = {"requestId": args.request_id or str(uuid.uuid4()), "message":
               "Windows native bridge acceptance. Reply only POCKET_WINDOWS_NATIVE_OK. Do not use tools or change files."}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://127.0.0.1:{server.server_port}/api/codex/threads/{args.thread_id}/turn"

    def send():
        request = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with opener.open(request, timeout=90) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    try:
        status, response = send()
        print(json.dumps({"requestId": payload["requestId"], "http": status, "result": response}), flush=True)
        if response.get("ok"):
            # Same request ID is intentionally replayed only AFTER confirmation;
            # the journal must return the receipt without a second UI action.
            status, replay = send()
            print(json.dumps({"replayHttp": status, "replay": replay}), flush=True)
            if not replay.get("desktop", {}).get("duplicateRequest"):
                raise RuntimeError("Idempotent receipt replay not confirmed")
        return 0 if response.get("ok") else 1
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        server.app_server.close()


if __name__ == "__main__":
    raise SystemExit(main())
