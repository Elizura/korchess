"use client";

import { useEffect } from "react";

import { AuthProvider, useAuth } from "@/lib/auth";
import RouteTracker from "@/components/analytics/RouteTracker";
import {
  identifyAnalyticsUser,
  initAnalytics,
  setAnalyticsAuthTokenGetter,
  trackEvent,
  withTrackingHeaders,
} from "@/lib/analytics/client";
import { API_BASE_URL } from "@/lib/api-url";

const getSessionStorageItem = (key: string): string | null => {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
};

const setSessionStorageItem = (key: string, value: string): void => {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Ignore storage failures in privacy-restricted browsers/extensions.
  }
};

function AnalyticsLinker() {
  const { user, accessToken, getAccessToken } = useAuth();

  useEffect(() => {
    initAnalytics();
  }, []);

  // Analytics reads the token when it sends, so renewals are picked up
  useEffect(() => {
    setAnalyticsAuthTokenGetter(getAccessToken);
    return () => setAnalyticsAuthTokenGetter(null);
  }, [getAccessToken]);

  useEffect(() => {
    if (!user?.id || !accessToken) return;

    const key = `analytics-linked:${user.id}`;
    if (getSessionStorageItem(key)) return;

    identifyAnalyticsUser(accessToken)
      .then((ok) => {
        if (ok) setSessionStorageItem(key, "1");
      })
      .catch(() => {});
  }, [user?.id, accessToken]);

  return null;
}

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <AnalyticsLinker />
      <RouteTracker />
      {children}
    </AuthProvider>
  );
}
