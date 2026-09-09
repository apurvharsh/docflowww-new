import React, { useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { authApi } from '../lib/api';
import Input from '../components/ui/Input';
import Button from '../components/ui/Button';

const GoogleIcon = (props) => (
  <svg viewBox="0 0 48 48" width="18" height="18" {...props}>
    <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.6-6 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z" />
    <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 15.9 18.9 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6 29.5 4 24 4 16.3 4 9.6 8.3 6.3 14.7z" />
    <path fill="#4CAF50" d="M24 44c5.4 0 10.3-1.8 14.1-5l-6.5-5.5C29.6 35.6 26.9 36.5 24 36.5c-5.3 0-9.7-3.4-11.3-8.1l-6.6 5.1C9.5 39.6 16.2 44 24 44z" />
    <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.5l6.5 5.5C41.3 36 44 30.5 44 24c0-1.3-.1-2.7-.4-3.5z" />
  </svg>
);

const LoginPage = () => {
  const { isAuthenticated, login, signup, loginSuperAdmin, loginWithGoogle } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const resetToken = searchParams.get('reset_token');
  const [mode, setMode] = useState(resetToken ? 'reset' : 'login'); // login | signup | forgot | reset
  const [form, setForm] = useState({
    email: '', password: '', full_name: '', organization: '', team_name: '', job_title: '',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (isAuthenticated) return <Navigate to="/" replace />;

  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(form.email, form.password);
      } else if (mode === 'signup') {
        await signup(form);
      } else if (mode === 'forgot') {
        await authApi.forgotPassword(form.email);
        setError('If an account exists, a password-reset email has been sent.');
      } else {
        await authApi.resetPassword(resetToken, form.password);
        setSearchParams({});
        setMode('login');
        setError('Password reset. You can now sign in.');
      }
    } catch (err) {
      setError(err.detail?.detail || err.message || 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  const handleSuperAdmin = async () => {
    setError('');
    setBusy(true);
    try {
      await loginSuperAdmin();
    } catch (err) {
      setError(err.message || 'Could not start super-admin access.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-5 lg:p-10">
      <div className="w-full max-w-5xl grid lg:grid-cols-[1fr_430px] gap-12 items-center">
        <div className="hidden lg:block px-8">
          <div className="flex items-center gap-2 text-sm font-bold uppercase tracking-[0.2em] text-primary mb-8"><Sparkles size={17} /> DocFlow AI</div>
          <h1 className="text-6xl font-bold tracking-tight text-gray-100 leading-[1.05]">Your work,<br /><span className="text-primary">in flow.</span></h1>
          <p className="text-lg text-gray-500 mt-6 max-w-md leading-relaxed">Turn scattered project documents into decisions, drafts, and momentum your whole team can see.</p>
          <div className="flex gap-8 mt-12 text-sm text-gray-500"><span><strong className="block text-2xl text-gray-900">01</strong>Collect sources</span><span><strong className="block text-2xl text-gray-900">02</strong>Ask &amp; analyze</span><span><strong className="block text-2xl text-gray-900">03</strong>Draft forward</span></div>
        </div>
        <div>
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 text-2xl font-bold text-gray-100">
            <Sparkles className="text-primary" size={26} />
            DocFlow <span className="text-primary">AI</span>
          </div>
          <p className="text-gray-400 mt-2">
            {mode === 'login' ? 'Sign in to your workspace' : 'Create your workspace account'}
          </p>
        </div>

        <div className="bg-surface border border-border rounded-xl p-6 shadow-lg shadow-black/20">
          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === 'signup' && (
              <>
                <Input label="Full name" required value={form.full_name} onChange={update('full_name')} />
                <div className="grid grid-cols-2 gap-3">
                  <Input label="Organization" required value={form.organization} onChange={update('organization')} />
                  <Input label="Team" required value={form.team_name} onChange={update('team_name')} placeholder="Engineering" />
                </div>
                <Input label="Job title" required value={form.job_title} onChange={update('job_title')} placeholder="Product Manager" />
              </>
            )}
            <Input label="Email" type="email" required value={form.email} onChange={update('email')} placeholder="you@company.com" />
            {mode !== 'forgot' && <Input label={mode === 'reset' ? 'New password' : 'Password'} type="password" required value={form.password} onChange={update('password')} placeholder="••••••••" />}

            {error && <p className="text-sm text-red-400">{error}</p>}

            <Button type="submit" className="w-full" loading={busy}>
              {mode === 'login' ? 'Sign in' : mode === 'signup' ? 'Create account' : mode === 'forgot' ? 'Send reset link' : 'Reset password'}
            </Button>
          </form>

          <div className="flex items-center gap-3 my-5">
            <div className="h-px bg-border flex-1" />
            <span className="text-xs text-gray-500 uppercase">or</span>
            <div className="h-px bg-border flex-1" />
          </div>

          {mode === 'login' && <div className="space-y-3">
            <Button
              type="button"
              variant="secondary"
              className="w-full"
              onClick={loginWithGoogle}
            >
              <GoogleIcon className="mr-2" />
              Continue with Google
            </Button>
            <Button type="button" variant="ghost" className="w-full" onClick={handleSuperAdmin} loading={busy}>
              Continue as Super Admin
            </Button>
          </div>}
          {mode === 'login' && (
            <button type="button" className="mt-4 w-full text-sm text-primary-light hover:underline" onClick={() => { setError(''); setMode('forgot'); }}>
              Forgot password?
            </button>
          )}
        </div>

        <p className="text-center text-sm text-gray-500 mt-6">
          {mode === 'login' || mode === 'forgot' || mode === 'reset' ? "Don't have an account? " : 'Already have an account? '}
          <button
            type="button"
            className="text-primary-light hover:underline"
            onClick={() => { setError(''); setMode(mode === 'signup' ? 'login' : 'signup'); }}
          >
            {mode === 'signup' ? 'Sign in' : 'Sign up'}
          </button>
        </p>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
