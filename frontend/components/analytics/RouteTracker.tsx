"use client";

import { useEffect, useMemo, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";

import { flushAnalytics, trackEvent } from "@/lib/analytics/client";

export default function RouteTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const currentPath = useMemo(() => {
    const query = searchParams?.toString();
    return query ? `${pathname}?${query}` : pathname;
  }, [pathname, searchParams]);

  const pageStartAtRef = useRef<number>(Date.now());
  const activePathRef = useRef<string>(currentPath);

  useEffect(() => {
    const previousPath = activePathRef.current;
    const now = Date.now();

    if (previousPath && previousPath !== currentPath) {
      trackEvent("page.leave", {
        path: previousPath,
        properties: {
          duration_ms: now - pageStartAtRef.current,
        },
      });
    }

    activePathRef.current = currentPath;
    pageStartAtRef.current = now;

    trackEvent("page.view", {
      path: currentPath,
      properties: {
        route: currentPath,
      },
    });
  }, [currentPath]);

  useEffect(() => {
    const handlePageLeave = () => {
      trackEvent("page.leave", {
        path: activePathRef.current,
        properties: {
          duration_ms: Date.now() - pageStartAtRef.current,
          lifecycle: "unload",
        },
      });
      void flushAnalytics({ useBeacon: true });
    };

    const handleVisibility = () => {
      if (document.visibilityState !== "hidden") return;
      trackEvent("page.leave", {
        path: activePathRef.current,
        properties: {
          duration_ms: Date.now() - pageStartAtRef.current,
          lifecycle: "hidden",
        },
      });
      void flushAnalytics({ useBeacon: true });
      pageStartAtRef.current = Date.now();
    };

    window.addEventListener("beforeunload", handlePageLeave);
    window.addEventListener("pagehide", handlePageLeave);
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      window.removeEventListener("beforeunload", handlePageLeave);
      window.removeEventListener("pagehide", handlePageLeave);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  return null;
}
