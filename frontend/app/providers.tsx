"use client";

import { SessionProvider, signOut, useSession } from "next-auth/react";
import { useEffect } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

function RegisterOnLogin() {
  const { data: session } = useSession();

  useEffect(() => {
    const authError = (session as any)?.authError as string | undefined;
    if (authError === "RefreshAccessTokenError") {
      signOut({ callbackUrl: "/signup" });
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
      headers: {
        Authorization: `Bearer ${idToken}`,
      },
    })
      .then((res) => {
        if (res.ok) {
          sessionStorage.setItem(key, "1");
        }
      })
      .catch(() => {
        // Silent; user will see 403 when hitting protected endpoints if register failed.
      });
  }, [session]);

  return null;
}

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider refetchInterval={5 * 60} refetchOnWindowFocus={true}>
      <RegisterOnLogin />
      {children}
    </SessionProvider>
  );
}
