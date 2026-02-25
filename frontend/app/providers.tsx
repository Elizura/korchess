"use client";

import { SessionProvider, signOut, useSession } from "next-auth/react";
import { useEffect } from "react";

import RouteTracker from "@/components/analytics/RouteTracker";
import {
  identifyAnalyticsUser,
  initAnalytics,
  setAnalyticsAuthToken,
  trackEvent,
  withTrackingHeaders,
} from "@/lib/analytics/client";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

function RegisterOnLogin() {
  const { data: session } = useSession();

  useEffect(() => {
    initAnalytics();
  }, []);

  useEffect(() => {
    setAnalyticsAuthToken((session as any)?.idToken || null);
  }, [session]);

  useEffect(() => {
    const authError = (session as any)?.authError as string | undefined;
    if (authError === "RefreshAccessTokenError") {
      const callbackUrl =
        typeof window !== "undefined"
          ? `${window.location.pathname}${window.location.search}`
          : "/dashboard";
      signOut({ callbackUrl });
      return;
    }

    const userId = (session as any)?.userId as string | undefined;
    const idToken = (session as any)?.idToken as string | undefined;
    if (!userId || !idToken) {
      return;
    }

    const key = `registered:${userId}`;
    if (sessionStorage.getItem(key)) {
      return;
    }

    fetch(`${API_BASE_URL}/api/v1/auth/register`, {
      method: "POST",
      headers: withTrackingHeaders({
        Authorization: `Bearer ${idToken}`,
      }),
    })
      .then(async (res) => {
        if (res.ok) {
          sessionStorage.setItem(key, "1");
          const data = await res.json().catch(() => ({}));
          if (data?.created) {
            trackEvent("auth.registered", {
              properties: {
                auth_provider: "google",
                source: "register_on_login",
              },
            });
          }
        }
      })
      .catch(() => {
        // Silent; user will see 403 when hitting protected endpoints if register failed.
      });
  }, [session]);

  useEffect(() => {
    const userId = (session as any)?.userId as string | undefined;
    const idToken = (session as any)?.idToken as string | undefined;
    if (!userId || !idToken) {
      return;
    }

    const key = `analytics-linked:${userId}`;
    if (sessionStorage.getItem(key)) {
      return;
    }

    identifyAnalyticsUser(idToken)
      .then((ok) => {
        if (ok) {
          sessionStorage.setItem(key, "1");
        }
      })
      .catch(() => {
        // Silent; linking can retry next session refresh.
      });
  }, [session]);

  return null;
}

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider refetchInterval={5 * 60} refetchOnWindowFocus={true}>
      <RegisterOnLogin />
      <RouteTracker />
      {children}
    </SessionProvider>
  );
}
