import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';
import { api } from '../../../utils/api';
import AuthErrorAlert from './AuthErrorAlert';
import AuthInputField from './AuthInputField';
import AuthScreenLayout from './AuthScreenLayout';

type LoginFormState = {
  username: string;
  password: string;
};

const initialState: LoginFormState = {
  username: '',
  password: '',
};

export default function LoginForm() {
  const { t } = useTranslation('auth');
  const { login } = useAuth();

  const [formState, setFormState] = useState<LoginFormState>(initialState);
  const [errorMessage, setErrorMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [pendingRequestToken, setPendingRequestToken] = useState<string | null>(null);
  const [approvalMessage, setApprovalMessage] = useState('');

  const updateField = useCallback((field: keyof LoginFormState, value: string) => {
    setFormState((previous) => ({ ...previous, [field]: value }));
  }, []);

  const submitLogin = useCallback(
    async (isAutoRetry = false) => {
      const result = await login(formState.username.trim(), formState.password);
      if (!result.success) {
        if (result.approvalRequired && result.requestToken) {
          setPendingRequestToken(result.requestToken);
          setApprovalMessage(isAutoRetry ? '电脑端尚未批准，请稍候...' : result.error);
          return;
        }
        setErrorMessage(result.error);
        setPendingRequestToken(null);
        setApprovalMessage('');
        return;
      }
      setPendingRequestToken(null);
      setApprovalMessage('');
    },
    [formState.password, formState.username, login],
  );

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setErrorMessage('');

      // Keep form validation local so each auth screen owns its own UI feedback.
      if (!formState.username.trim() || !formState.password) {
        setErrorMessage(t('login.errors.requiredFields'));
        return;
      }

      setIsSubmitting(true);
      await submitLogin(false);
      setIsSubmitting(false);
    },
    [formState.password, formState.username, submitLogin, t],
  );

  useEffect(() => {
    if (!pendingRequestToken) {
      return undefined;
    }

    let cancelled = false;
    let timer = 0;
    let polling = false;

    const pollStatus = async () => {
      if (cancelled || polling) {
        return;
      }

      polling = true;
      try {
        const response = await api.auth.deviceApprovalStatus(pendingRequestToken);
        const payload = await response.json().catch(() => null);
        const status = payload?.approvalStatus;

        if (status === 'approved') {
          cancelled = true;
          setApprovalMessage('电脑端已批准，正在完成登录...');
          setIsSubmitting(true);
          setErrorMessage('');
          await submitLogin(true);
          setIsSubmitting(false);
          return;
        }

        if (status === 'rejected') {
          cancelled = true;
          setPendingRequestToken(null);
          setApprovalMessage('');
          setErrorMessage(payload?.message || '这台设备的登录申请已被电脑端拒绝。');
          return;
        }

        setApprovalMessage(payload?.message || '等待电脑端批准这台设备...');
      } catch (error) {
        setApprovalMessage('正在等待电脑端批准，状态轮询暂时失败，稍后会重试。');
      } finally {
        polling = false;
        if (!cancelled) {
          timer = window.setTimeout(() => {
            void pollStatus();
          }, 3000);
        }
      }
    };

    void pollStatus();

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pendingRequestToken, submitLogin]);

  return (
    <AuthScreenLayout
      title={t('login.title')}
      description={t('login.description')}
      footerText="请输入你的账号和密码，登录后即可控制这台电脑上的 Codex。"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <AuthInputField
          id="username"
          label={t('login.username')}
          value={formState.username}
          onChange={(value) => updateField('username', value)}
          placeholder={t('login.placeholders.username')}
          isDisabled={isSubmitting}
        />

        <AuthInputField
          id="password"
          label={t('login.password')}
          value={formState.password}
          onChange={(value) => updateField('password', value)}
          placeholder={t('login.placeholders.password')}
          isDisabled={isSubmitting}
          type="password"
        />

        <AuthErrorAlert errorMessage={errorMessage} />

        {pendingRequestToken ? (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {approvalMessage || '等待电脑端批准这台设备...'}
          </div>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-blue-600 px-4 py-2 font-medium text-white transition-colors duration-200 hover:bg-blue-700 disabled:bg-blue-400"
        >
          {isSubmitting ? t('login.loading') : t('login.submit')}
        </button>
      </form>
    </AuthScreenLayout>
  );
}
