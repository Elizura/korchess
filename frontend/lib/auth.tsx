"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { API_BASE_URL } from "@/lib/api-url";

export interface AuthUser {
  id: string;
  email: string;
  name?: string;
  avatar?: string;
  username?: string;
  avatar_url?: string;
  onboarding_complete?: boolean;
}

interface AuthContextValue {
  user: AuthUser | null;
  accessToken: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  signup: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  signin: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  googleSignin: (idToken: string) => Promise<{ ok: boolean; error?: string }>;
  verify: (
    email: string,
    code: string,
    password?: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  hasPendingSignup: (email: string) => boolean;
  resendCode: (email: string) => Promise<{ ok: boolean; error?: string }>;
  requestPasswordReset: (email: string) => Promise<{ ok: boolean; error?: string }>;
  resetPassword: (
    email: string,
    code: string,
    password: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  signout: () => Promise<void>;
  getAuthHeaders: () => Promise<Record<string, string>>;
  /** Current access token, read at call time (no refresh) */
  getAccessToken: () => string | null;
  refreshSession: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function decodeTokenExp(token: string): number | null {
  try {
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

function isTokenExpiringSoon(token: string, thresholdSec = 60): boolean {
  const exp = decodeTokenExp(token);
  if (!exp) return true;
  return Date.now() / 1000 > exp - thresholdSec;
}

async function postEmailRequest(
  path: string,
  email: string,
  fallbackError: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/auth/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: typeof data.detail === "string" ? data.detail : fallbackError };
    }
    return { ok: true };
  } catch {
    return { ok: false, error: "Network error." };
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshPromiseRef = useRef<Promise<boolean> | null>(null);
  const accessTokenRef = useRef<string | null>(null);
  // Password typed during sign-up (or an unverified sign-in), held in memory
  // only until the email code is confirmed; the backend requires it to verify
  const pendingSignupRef = useRef<{ email: string; password: string } | null>(null);

  const isAuthenticated = useMemo(() => !!user && !!accessToken, [user, accessToken]);

  const fetchMe = useCallback(async (token: string): Promise<AuthUser | null> => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }, []);

  const refreshSession = useCallback(async (): Promise<boolean> => {
    if (refreshPromiseRef.current) return refreshPromiseRef.current;

    const promise = (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) {
          setUser(null);
          setAccessToken(null);
          accessTokenRef.current = null;
          return false;
        }
        const data = await res.json();
        const token = data.access_token as string;
        setAccessToken(token);
        accessTokenRef.current = token;

        const me = await fetchMe(token);
        setUser(me);
        return !!me;
      } catch {
        // Network error: keep the current session so a scheduled renewal can retry
        return false;
      } finally {
        refreshPromiseRef.current = null;
      }
    })();

    refreshPromiseRef.current = promise;
    return promise;
  }, [fetchMe]);

  useEffect(() => {
    const path = typeof window !== "undefined" ? window.location.pathname : "";
    const isPublicAuthPage = ["/", "/signin", "/signup", "/verify", "/forgot-password"].includes(path);
    if (isPublicAuthPage) {
      setIsLoading(false);
      return;
    }
    refreshSession().finally(() => setIsLoading(false));
  }, [refreshSession]);

  // Access tokens are short-lived. Renew shortly before expiry so pages that
  // build headers from `accessToken` never send an expired one.
  useEffect(() => {
    if (!accessToken) return;
    const exp = decodeTokenExp(accessToken);
    if (!exp) return;

    let timer: ReturnType<typeof setTimeout>;
    const schedule = (delayMs: number) => {
      timer = setTimeout(async () => {
        const ok = await refreshSession();
        // Token unchanged means the refresh hit a network error; retry soon
        if (!ok && accessTokenRef.current === accessToken) schedule(30_000);
      }, delayMs);
    };
    schedule(Math.max((exp - 60) * 1000 - Date.now(), 0));
    return () => clearTimeout(timer);
  }, [accessToken, refreshSession]);

  // Timers stall while a device sleeps; catch up when the tab is used again.
  useEffect(() => {
    const renewIfStale = () => {
      if (document.visibilityState !== "visible") return;
      const token = accessTokenRef.current;
      if (token && isTokenExpiringSoon(token)) void refreshSession();
    };
    document.addEventListener("visibilitychange", renewIfStale);
    window.addEventListener("focus", renewIfStale);
    return () => {
      document.removeEventListener("visibilitychange", renewIfStale);
      window.removeEventListener("focus", renewIfStale);
    };
  }, [refreshSession]);

  const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
    let token = accessTokenRef.current;
    if (!token || isTokenExpiringSoon(token)) {
      const ok = await refreshSession();
      if (!ok) return {};
      token = accessTokenRef.current;
    }
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }, [refreshSession]);

  const getAccessToken = useCallback(() => accessTokenRef.current, []);

  const signup = useCallback(async (email: string, password: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) return { ok: false, error: data.detail || "Signup failed." };
      pendingSignupRef.current = { email: email.trim().toLowerCase(), password };
      return { ok: true };
    } catch {
      return { ok: false, error: "Network error." };
    }
  }, []);

  const hasPendingSignup = useCallback(
    (email: string) => pendingSignupRef.current?.email === email.trim().toLowerCase(),
    [],
  );

  const verify = useCallback(async (email: string, code: string, password?: string) => {
    const pending = pendingSignupRef.current;
    const signupPassword =
      password ?? (pending?.email === email.trim().toLowerCase() ? pending.password : "");
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code, password: signupPassword }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) return { ok: false, error: data.detail || "Verification failed." };

      pendingSignupRef.current = null;
      const token = data.access_token as string;
      setAccessToken(token);
      accessTokenRef.current = token;
      const me = await fetchMe(token);
      setUser(me);
      return { ok: true };
    } catch {
      return { ok: false, error: "Network error." };
    }
  }, [fetchMe]);

  const resendCode = useCallback(
    (email: string) => postEmailRequest("resend-code", email, "Couldn't send a new code."),
    [],
  );

  const requestPasswordReset = useCallback(
    (email: string) => postEmailRequest("forgot-password", email, "Couldn't send a reset code."),
    [],
  );

  const resetPassword = useCallback(async (email: string, code: string, password: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code, password }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) {
        return {
          ok: false,
          error: typeof data.detail === "string" ? data.detail : "Password reset failed.",
        };
      }

      const token = data.access_token as string;
      setAccessToken(token);
      accessTokenRef.current = token;
      const me = await fetchMe(token);
      setUser(me);
      return { ok: true };
    } catch {
      return { ok: false, error: "Network error." };
    }
  }, [fetchMe]);

  const signin = useCallback(async (email: string, password: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/signin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });
      const data = await res.json();
      if (res.status === 403) {
        // Correct password but unverified email: keep it for the verify step
        pendingSignupRef.current = { email: email.trim().toLowerCase(), password };
      }
      if (!res.ok) return { ok: false, error: data.detail || "Sign in failed." };

      const token = data.access_token as string;
      setAccessToken(token);
      accessTokenRef.current = token;
      const me = await fetchMe(token);
      setUser(me);
      return { ok: true };
    } catch {
      return { ok: false, error: "Network error." };
    }
  }, [fetchMe]);

  const googleSignin = useCallback(async (idToken: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) return { ok: false, error: data.detail || "Google sign in failed." };

      const token = data.access_token as string;
      setAccessToken(token);
      accessTokenRef.current = token;
      const me = await fetchMe(token);
      setUser(me);
      return { ok: true };
    } catch {
      return { ok: false, error: "Network error." };
    }
  }, [fetchMe]);

  const signout = useCallback(async () => {
    try {
      await fetch(`${API_BASE_URL}/api/v1/auth/signout`, {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // best-effort
    }
    setUser(null);
    setAccessToken(null);
    accessTokenRef.current = null;
    pendingSignupRef.current = null;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      accessToken,
      isLoading,
      isAuthenticated,
      signup,
      signin,
      googleSignin,
      verify,
      hasPendingSignup,
      resendCode,
      requestPasswordReset,
      resetPassword,
      signout,
      getAuthHeaders,
      getAccessToken,
      refreshSession,
    }),
    [
      user, accessToken, isLoading, isAuthenticated, signup, signin, googleSignin, verify,
      hasPendingSignup, resendCode, requestPasswordReset, resetPassword, signout, getAuthHeaders, getAccessToken,
      refreshSession,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
