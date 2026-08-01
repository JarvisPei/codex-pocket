#!/usr/bin/env node

import crypto from "node:crypto";
import net from "node:net";

const socketPath =
  process.env.CODEX_APP_SERVER_SOCKET ??
  `${process.env.HOME}/.codex/app-server-control/app-server-control.sock`;

if (socketPath.endsWith("/.codex/ipc/ipc.sock")) {
  console.error(
    "Refusing to probe ~/.codex/ipc/ipc.sock: it is the IDE-context router, not an app-server endpoint.",
  );
  process.exit(2);
}

const socket = net.createConnection(socketPath);
const websocketKey = crypto.randomBytes(16).toString("base64");
let buffer = Buffer.alloc(0);
let upgraded = false;
let initialized = false;
let finished = false;

function finish(exitCode, message) {
  if (finished) return;
  finished = true;
  clearTimeout(timeout);
  if (message) console.log(message);
  socket.end();
  process.exitCode = exitCode;
}

function encodeTextFrame(value) {
  const payload = Buffer.from(JSON.stringify(value));
  const mask = crypto.randomBytes(4);
  let header;
  if (payload.length < 126) {
    header = Buffer.from([0x81, 0x80 | payload.length]);
  } else if (payload.length <= 0xffff) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 0x80 | 126;
    header.writeUInt16BE(payload.length, 2);
  } else {
    throw new Error("Probe request is unexpectedly large.");
  }
  const masked = Buffer.alloc(payload.length);
  for (let index = 0; index < payload.length; index += 1) {
    masked[index] = payload[index] ^ mask[index % 4];
  }
  return Buffer.concat([header, mask, masked]);
}

function send(value) {
  socket.write(encodeTextFrame(value));
}

function readFrame() {
  if (buffer.length < 2) return null;
  const opcode = buffer[0] & 0x0f;
  let length = buffer[1] & 0x7f;
  let offset = 2;
  if (length === 126) {
    if (buffer.length < 4) return null;
    length = buffer.readUInt16BE(2);
    offset = 4;
  } else if (length === 127) {
    if (buffer.length < 10) return null;
    const largeLength = buffer.readBigUInt64BE(2);
    if (largeLength > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new Error("Server frame is too large.");
    }
    length = Number(largeLength);
    offset = 10;
  }
  if (buffer.length < offset + length) return null;
  const payload = buffer.subarray(offset, offset + length);
  buffer = buffer.subarray(offset + length);
  return { opcode, payload };
}

function handleMessage(message) {
  if (message.id === 1 && message.result && !initialized) {
    initialized = true;
    send({ method: "initialized" });
    send({
      id: 2,
      method: "thread/list",
      params: {
        limit: 20,
        sortKey: "updated_at",
        sortDirection: "desc",
        useStateDbOnly: true,
      },
    });
    return;
  }

  if ((message.id === 1 || message.id === 2) && message.error) {
    finish(1, `App-server request failed: ${message.error.message}`);
    return;
  }

  if (message.id === 2 && message.result) {
    const threads = message.result.data ?? [];
    const summary = threads.map((thread) => ({
      id: thread.id,
      title: thread.name ?? thread.preview ?? null,
      cwd: thread.cwd ?? null,
      status:
        typeof thread.status === "object"
          ? thread.status.type ?? "unknown"
          : thread.status ?? "unknown",
      updatedAt: thread.updatedAt ?? null,
    }));
    finish(0, JSON.stringify({ count: summary.length, threads: summary }, null, 2));
  }
}

socket.on("connect", () => {
  socket.write(
    [
      "GET /rpc HTTP/1.1",
      "Host: localhost",
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Key: ${websocketKey}`,
      "Sec-WebSocket-Version: 13",
      "",
      "",
    ].join("\r\n"),
  );
});

socket.on("data", (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  if (!upgraded) {
    const headerEnd = buffer.indexOf("\r\n\r\n");
    if (headerEnd === -1) return;
    const headers = buffer.subarray(0, headerEnd).toString();
    buffer = buffer.subarray(headerEnd + 4);
    if (!headers.startsWith("HTTP/1.1 101")) {
      finish(1, `WebSocket upgrade failed: ${headers.split("\r\n")[0]}`);
      return;
    }
    upgraded = true;
    send({
      id: 1,
      method: "initialize",
      params: {
        clientInfo: {
          name: "codex-pocket-probe",
          version: "0.0.1",
        },
        capabilities: { experimentalApi: true },
      },
    });
  }

  let frame;
  while ((frame = readFrame())) {
    if (frame.opcode === 0x1) {
      handleMessage(JSON.parse(frame.payload.toString()));
    } else if (frame.opcode === 0x8) {
      finish(1, "App-server closed the WebSocket before responding.");
    }
  }
});

socket.on("error", (error) => {
  finish(1, `Failed to connect to app-server: ${error.message}`);
});

const timeout = setTimeout(() => {
  finish(1, "App-server probe timed out.");
}, 10_000);
