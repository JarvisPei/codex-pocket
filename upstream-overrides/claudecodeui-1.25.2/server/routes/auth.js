import express from 'express';
import bcrypt from 'bcrypt';
import crypto from 'crypto';
import { userDb, db, trustedDevicesDb } from '../database/db.js';
import {
  authenticateToken,
  clearAuthCookie,
  generateToken,
  setAuthCookie,
} from '../middleware/auth.js';

const router = express.Router();
const sanitizeUser = (user) => ({ id: user.id, username: user.username });

const getRequestIp = (req) => {
  const forwardedFor = req.headers['x-forwarded-for'];
  if (typeof forwardedFor === 'string' && forwardedFor.trim()) {
    return forwardedFor.split(',')[0].trim();
  }

  return req.ip || req.socket?.remoteAddress || null;
};

const normalizeTextField = (value, maxLength = 160) => {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  return trimmed.slice(0, maxLength);
};

const getDeviceMetadataFromRequest = (req) => ({
  deviceId: normalizeTextField(req.body?.deviceId, 128),
  deviceName: normalizeTextField(req.body?.deviceName, 160),
  platform: normalizeTextField(req.body?.platform, 80),
  appType: normalizeTextField(req.body?.appType, 80),
  ip: getRequestIp(req),
  userAgent: normalizeTextField(req.headers['user-agent'], 512),
});

const buildApprovalPayload = (request, message = '新设备需要在电脑端批准后才能登录。') => ({
  success: false,
  approvalRequired: true,
  requestToken: request.request_token,
  approvalStatus: request.status,
  message,
  deviceName: request.device_name || request.device_id,
});

const issueAuthSession = (req, res, user, deviceMetadata = null) => {
  const token = generateToken(user, {
    deviceId: deviceMetadata?.deviceId || null,
    deviceName: deviceMetadata?.deviceName || null,
    appType: deviceMetadata?.appType || null,
  });
  setAuthCookie(res, token, req);

  return {
    success: true,
    token,
    user: sanitizeUser(user),
  };
};

// Check auth status and setup requirements
router.get('/status', async (req, res) => {
  try {
    const hasUsers = await userDb.hasUsers();
    res.json({ 
      needsSetup: !hasUsers,
      isAuthenticated: false // Will be overridden by frontend if token exists
    });
  } catch (error) {
    console.error('Auth status error:', error);
    res.status(500).json({ error: '服务器内部错误' });
  }
});

router.get('/device-approval/:requestToken', async (req, res) => {
  try {
    const requestToken = normalizeTextField(req.params.requestToken, 128);
    if (!requestToken) {
      return res.status(400).json({ error: '审批令牌无效' });
    }

    const request = trustedDevicesDb.getApprovalRequestByToken(requestToken);
    if (!request) {
      return res.status(404).json({ error: '未找到对应的审批申请' });
    }

    return res.json({
      success: true,
      approvalStatus: request.status,
      message:
        request.status === 'approved'
          ? '设备已获批准，请重新完成登录。'
          : request.status === 'rejected'
            ? '这台设备的登录申请已被电脑端拒绝。'
            : '等待电脑端批准这台设备。',
    });
  } catch (error) {
    console.error('Device approval status error:', error);
    res.status(500).json({ error: '服务器内部错误' });
  }
});

// User registration (setup) - only allowed if no users exist
router.post('/register', async (req, res) => {
  try {
    const { username, password } = req.body;
    const deviceMetadata = getDeviceMetadataFromRequest(req);
    
    // Validate input
    if (!username || !password) {
      return res.status(400).json({ error: '请输入用户名和密码' });
    }
    
    if (username.length < 3 || password.length < 6) {
      return res.status(400).json({ error: '用户名至少 3 个字符，密码至少 6 个字符' });
    }
    
    // Use a transaction to prevent race conditions
    db.prepare('BEGIN').run();
    try {
      // Check if users already exist (only allow one user)
      const hasUsers = userDb.hasUsers();
      if (hasUsers) {
        db.prepare('ROLLBACK').run();
        return res.status(403).json({ error: '用户已存在。当前系统仅允许单用户使用。' });
      }
      
      // Hash password
      const saltRounds = 12;
      const passwordHash = await bcrypt.hash(password, saltRounds);
      
      // Create user
      const user = userDb.createUser(username, passwordHash);

      if (deviceMetadata.deviceId) {
        trustedDevicesDb.approveDevice(user.id, deviceMetadata.deviceId, deviceMetadata);
      }
      
      db.prepare('COMMIT').run();

      // Update last login (non-fatal, outside transaction)
      userDb.updateLastLogin(user.id);

      res.json(issueAuthSession(req, res, user, deviceMetadata.deviceId ? deviceMetadata : null));
    } catch (error) {
      db.prepare('ROLLBACK').run();
      throw error;
    }
    
  } catch (error) {
    console.error('Registration error:', error);
    if (error.code === 'SQLITE_CONSTRAINT_UNIQUE') {
      res.status(409).json({ error: '用户名已存在' });
    } else {
      res.status(500).json({ error: '服务器内部错误' });
    }
  }
});

// User login
router.post('/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    const deviceMetadata = getDeviceMetadataFromRequest(req);
    
    // Validate input
    if (!username || !password) {
      return res.status(400).json({ error: '请输入用户名和密码' });
    }

    if (!deviceMetadata.deviceId) {
      return res.status(400).json({ error: '当前客户端没有发送设备标识，请刷新后重试。' });
    }
    
    // Get user from database
    const user = userDb.getUserByUsername(username);
    if (!user) {
      return res.status(401).json({ error: '用户名或密码错误' });
    }
    
    // Verify password
    const isValidPassword = await bcrypt.compare(password, user.password_hash);
    if (!isValidPassword) {
      return res.status(401).json({ error: '用户名或密码错误' });
    }

    const approvedDevice = trustedDevicesDb.getApprovedDevice(user.id, deviceMetadata.deviceId);
    if (!approvedDevice) {
      const requestToken = crypto.randomBytes(24).toString('hex');
      const request = trustedDevicesDb.createOrRefreshPendingApproval(user.id, deviceMetadata.deviceId, requestToken, deviceMetadata);
      return res.status(202).json(buildApprovalPayload(request));
    }

    trustedDevicesDb.touchApprovedDevice(user.id, deviceMetadata.deviceId, {
      ...deviceMetadata,
      updateLogin: true,
    });
    
    // Update last login
    userDb.updateLastLogin(user.id);
    
    res.json(issueAuthSession(req, res, user, deviceMetadata));
    
  } catch (error) {
    console.error('Login error:', error);
    res.status(500).json({ error: '服务器内部错误' });
  }
});

// Get current user (protected route)
router.get('/user', authenticateToken, (req, res) => {
  res.json({
    user: sanitizeUser(req.user)
  });
});

// Logout should always clear the auth cookie, even if the current token is already invalid.
router.post('/logout', (req, res) => {
  clearAuthCookie(res, req);
  res.json({ success: true, message: 'Logged out successfully' });
});

export default router;
