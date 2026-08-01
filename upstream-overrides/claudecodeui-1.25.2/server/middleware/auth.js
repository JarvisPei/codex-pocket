import jwt from 'jsonwebtoken';
import { userDb, appConfigDb, trustedDevicesDb } from '../database/db.js';
import { IS_PLATFORM } from '../constants/config.js';

const JWT_SECRET = process.env.JWT_SECRET || appConfigDb.getOrCreateJwtSecret();
const AUTH_COOKIE_NAME = process.env.AUTH_COOKIE_NAME || 'codex_auth';
const TOKEN_LIFETIME = '7d';
const AUTH_COOKIE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

const parseCookieHeader = (cookieHeader) => {
  if (!cookieHeader) {
    return {};
  }

  return cookieHeader
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .reduce((cookies, part) => {
      const separatorIndex = part.indexOf('=');
      if (separatorIndex <= 0) {
        return cookies;
      }

      const key = part.slice(0, separatorIndex).trim();
      const value = decodeURIComponent(part.slice(separatorIndex + 1).trim());
      cookies[key] = value;
      return cookies;
    }, {});
};

const getBearerTokenFromRequest = (req) => {
  const authHeader = req.headers['authorization'];
  return authHeader && authHeader.startsWith('Bearer ')
    ? authHeader.slice('Bearer '.length)
    : null;
};

const getQueryTokenFromRequest = (req) => {
  try {
    const requestUrl = new URL(req.url, 'http://localhost');
    return requestUrl.searchParams.get('token') || null;
  } catch {
    return null;
  }
};

const isSecureRequest = (req) => {
  if (process.env.AUTH_COOKIE_SECURE === 'true') {
    return true;
  }

  if (process.env.AUTH_COOKIE_SECURE === 'false') {
    return false;
  }

  const forwardedProto = req?.headers?.['x-forwarded-proto'];
  return Boolean(req?.secure || forwardedProto === 'https');
};

const getAuthCookieOptions = (req) => ({
  httpOnly: true,
  sameSite: 'strict',
  secure: isSecureRequest(req),
  maxAge: AUTH_COOKIE_MAX_AGE_MS,
  path: '/',
});

const sanitizeUser = (user) => ({
  id: user.id,
  username: user.username,
});

const getAuthTokenFromRequest = (req) => {
  const cookies = parseCookieHeader(req.headers.cookie);
  return cookies[AUTH_COOKIE_NAME] || getBearerTokenFromRequest(req) || null;
};

const setAuthCookie = (res, token, req) => {
  res.cookie(AUTH_COOKIE_NAME, token, getAuthCookieOptions(req));
};

const clearAuthCookie = (res, req) => {
  res.clearCookie(AUTH_COOKIE_NAME, {
    httpOnly: true,
    sameSite: 'strict',
    secure: isSecureRequest(req),
    path: '/',
  });
};

const validateTrustedDeviceFromPayload = (user, payload) => {
  if (!payload?.deviceId) {
    return { ok: true, device: null };
  }

  const device = trustedDevicesDb.getApprovedDevice(user.id, payload.deviceId);
  if (!device) {
    return { ok: false, device: null };
  }

  return { ok: true, device };
};

const validateApiKey = (req, res, next) => {
  if (!process.env.API_KEY) {
    return next();
  }

  const apiKey = req.headers['x-api-key'];
  if (apiKey !== process.env.API_KEY) {
    return res.status(401).json({ error: 'Invalid API key' });
  }
  next();
};

const authenticateToken = async (req, res, next) => {
  if (IS_PLATFORM) {
    try {
      const user = userDb.getFirstUser();
      if (!user) {
        return res.status(500).json({ error: 'Platform mode: No user found in database' });
      }
      req.user = sanitizeUser(user);
      return next();
    } catch (error) {
      console.error('Platform mode error:', error);
      return res.status(500).json({ error: 'Platform mode: Failed to fetch user' });
    }
  }

  const token = getAuthTokenFromRequest(req);
  if (!token) {
    return res.status(401).json({ error: 'Access denied. No token provided.' });
  }

  try {
    const decoded = jwt.verify(token, JWT_SECRET);
    const user = userDb.getUserById(decoded.userId);
    if (!user) {
      clearAuthCookie(res, req);
      return res.status(401).json({ error: 'Invalid token. User not found.' });
    }

    const trustedDevice = validateTrustedDeviceFromPayload(user, decoded);
    if (!trustedDevice.ok) {
      clearAuthCookie(res, req);
      return res.status(403).json({ error: 'This device is no longer approved.' });
    }

    if (decoded.exp && decoded.iat) {
      const now = Math.floor(Date.now() / 1000);
      const halfLife = (decoded.exp - decoded.iat) / 2;
      if (now > decoded.iat + halfLife) {
        setAuthCookie(
          res,
          generateToken(user, {
            deviceId: decoded.deviceId || null,
            deviceName: decoded.deviceName || null,
            appType: decoded.appType || null,
          }),
          req,
        );
      }
    }

    req.user = sanitizeUser(user);
    req.device = trustedDevice.device ? {
      id: trustedDevice.device.id,
      deviceId: trustedDevice.device.device_id,
      deviceName: trustedDevice.device.device_name,
      appType: trustedDevice.device.app_type,
      platform: trustedDevice.device.platform,
    } : null;
    next();
  } catch (error) {
    console.error('Token verification error:', error);
    clearAuthCookie(res, req);
    return res.status(403).json({ error: 'Invalid token' });
  }
};

const generateToken = (user, options = {}) =>
  jwt.sign(
    {
      userId: user.id,
      username: user.username,
      ...(options.deviceId ? { deviceId: options.deviceId } : {}),
      ...(options.deviceName ? { deviceName: options.deviceName } : {}),
      ...(options.appType ? { appType: options.appType } : {}),
    },
    JWT_SECRET,
    { expiresIn: TOKEN_LIFETIME },
  );

const authenticateWebSocket = (token) => {
  if (IS_PLATFORM) {
    try {
      const user = userDb.getFirstUser();
      return user ? sanitizeUser(user) : null;
    } catch (error) {
      console.error('Platform mode WebSocket error:', error);
      return null;
    }
  }

  if (!token) {
    return null;
  }

  try {
    const decoded = jwt.verify(token, JWT_SECRET);
    const user = userDb.getUserById(decoded.userId);
    if (!user) {
      return null;
    }

    const trustedDevice = validateTrustedDeviceFromPayload(user, decoded);
    if (!trustedDevice.ok) {
      return null;
    }

    return sanitizeUser(user);
  } catch (error) {
    console.error('WebSocket token verification error:', error);
    return null;
  }
};

const authenticateWebSocketRequest = (req) =>
  authenticateWebSocket(getAuthTokenFromRequest(req) || getQueryTokenFromRequest(req));

export {
  AUTH_COOKIE_NAME,
  JWT_SECRET,
  authenticateToken,
  authenticateWebSocket,
  authenticateWebSocketRequest,
  clearAuthCookie,
  generateToken,
  getAuthTokenFromRequest,
  setAuthCookie,
  validateApiKey,
};
