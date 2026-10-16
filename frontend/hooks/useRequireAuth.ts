"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { withNextParam } from "@/lib/safeNext";

/**
 * For pages that need a signed-in user. Once auth has finished loading, a
 * visitor without a session is sent to /signin and brought back to this exact
 * page (path and query) afterwards.
 *
 * Returns true once requests can be made with an access token, so data
 * effects can wait for it instead of firing without an Authorization header.
 */
export function useRequireAuth(): boolean {
  const { isLoading, accessToken } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading || accessToken) return;
    const here = `${window.location.pathname}${window.location.search}`;
    router.replace(withNextParam("/signin", here));
  }, [isLoading, accessToken, router]);

  return !isLoading && !!accessToken;
}
