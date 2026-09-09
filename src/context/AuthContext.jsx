import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { authApi, getToken, setToken } from '../lib/api';

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await authApi.me();
      setUser(me);
    } catch {
      setToken(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // On first mount: if Google OAuth just redirected back with ?auth_token=...,
  // capture it, strip it from the URL, then load the session normally.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const incomingToken = params.get('auth_token');
    if (incomingToken) {
      setToken(incomingToken);
      params.delete('auth_token');
      const clean = window.location.pathname + (params.toString() ? `?${params}` : '');
      window.history.replaceState({}, '', clean);
    }
    loadUser();
  }, [loadUser]);

  const login = async (email, password) => {
    const { access_token } = await authApi.login(email, password);
    setToken(access_token);
    await loadUser();
  };

  const signup = async (data) => {
    return authApi.signup(data);
  };

  const loginSuperAdmin = async () => {
    const { access_token } = await authApi.superAdmin();
    setToken(access_token);
    await loadUser();
  };

  const loginWithGoogle = () => {
    window.location.href = authApi.googleStartUrl();
  };

  const logout = () => {
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAuthenticated: !!user,
        login,
        signup,
        loginSuperAdmin,
        loginDemo: loginSuperAdmin,
        loginWithGoogle,
        logout,
        refresh: loadUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
