const DEVICE_SESSION_STORAGE_KEY = 'codex-device-session-v1';
const DEVICE_ID_STORAGE_KEY = 'codex-device-id-v1';

function hasWindow() {
  return typeof window !== 'undefined';
}

function readStorage(key) {
  if (!hasWindow()) {
    return null;
  }

  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  if (!hasWindow()) {
    return;
  }

  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore storage failures in private/incognito WebViews.
  }
}

function removeStorage(key) {
  if (!hasWindow()) {
    return;
  }

  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore storage failures in private/incognito WebViews.
  }
}

function inferAppType() {
  if (!hasWindow()) {
    return 'unknown';
  }

  const standalone = window.matchMedia?.('(display-mode: standalone)')?.matches || window.navigator?.standalone;
  const ua = window.navigator?.userAgent || '';
  if (standalone) {
    return 'standalone';
  }
  if (/; wv\)|webview|version\/[\d.]+ chrome\/[\d.]+ mobile safari/i.test(ua)) {
    return 'webview';
  }
  return 'browser';
}

function inferDeviceName() {
  if (!hasWindow()) {
    return 'unknown-device';
  }

  const platform = window.navigator?.platform || '';
  const userAgent = window.navigator?.userAgent || '';
  if (/android/i.test(userAgent)) {
    return inferAppType() === 'webview' ? 'Android 封装 App' : 'Android 浏览器';
  }
  if (/iphone|ipad|ios/i.test(userAgent)) {
    return inferAppType() === 'webview' ? 'iPhone/iPad 封装 App' : 'iPhone/iPad 浏览器';
  }
  return platform || '浏览器设备';
}

export function getOrCreateDeviceId() {
  const existing = readStorage(DEVICE_ID_STORAGE_KEY);
  if (existing) {
    return existing;
  }

  const generated = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `device-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
  writeStorage(DEVICE_ID_STORAGE_KEY, generated);
  return generated;
}

export function getDeviceIdentity() {
  return {
    deviceId: getOrCreateDeviceId(),
    deviceName: inferDeviceName(),
    platform: hasWindow() ? window.navigator?.platform || window.navigator?.userAgent || 'unknown' : 'unknown',
    appType: inferAppType(),
  };
}

export function getStoredDeviceSession() {
  const raw = readStorage(DEVICE_SESSION_STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function storeDeviceSession(payload) {
  writeStorage(DEVICE_SESSION_STORAGE_KEY, JSON.stringify(payload));
}

export function clearDeviceSession() {
  removeStorage(DEVICE_SESSION_STORAGE_KEY);
}

