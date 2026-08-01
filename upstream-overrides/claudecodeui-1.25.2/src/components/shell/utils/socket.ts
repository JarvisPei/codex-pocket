import { IS_PLATFORM } from '../../../constants/config';
import { getStoredDeviceSession } from '../../auth/deviceTrust.js';
import type { ShellIncomingMessage, ShellOutgoingMessage } from '../types/types';

export function getShellWebSocketUrl(): string | null {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = new URL(`${protocol}//${window.location.host}/shell`);
  const deviceSession = getStoredDeviceSession();
  if (deviceSession?.token) {
    url.searchParams.set('token', deviceSession.token);
  }

  if (IS_PLATFORM) {
    return url.toString();
  }
  return url.toString();
}

export function parseShellMessage(payload: string): ShellIncomingMessage | null {
  try {
    return JSON.parse(payload) as ShellIncomingMessage;
  } catch {
    return null;
  }
}

export function sendSocketMessage(ws: WebSocket | null, message: ShellOutgoingMessage): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
}
