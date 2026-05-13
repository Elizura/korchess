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
  verify: (email: string, code: string) => Promise<{ ok: boolean; error?: string }>;
  signout: () => Promise<void>;
  getAuthHeaders: () => Promise<Record<string, string>>;
  refreshSession: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function decodeTokenExp(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshPromiseRef = useRef<Promise<boolean> | null>(null);
  const accessTokenRef = useRef<string | null>(null);

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
        setUser(null);
        setAccessToken(null);
        accessTokenRef.current = null;
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
    const isPublicAuthPage = ["/", "/signin", "/signup", "/verify"].includes(path);
    if (isPublicAuthPage) {
      setIsLoading(false);
      return;
    }
    refreshSession().finally(() => setIsLoading(false));
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
      return { ok: true };
    } catch {
      return { ok: false, error: "Network error." };
    }
  }, []);

  const verify = useCallback(async (email: string, code: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) return { ok: false, error: data.detail || "Verification failed." };

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
      signout,
      getAuthHeaders,
      refreshSession,
    }),
    [user, accessToken, isLoading, isAuthenticated, signup, signin, googleSignin, verify, signout, getAuthHeaders, refreshSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
