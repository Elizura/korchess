// Auth pages make no sense (or loop) as a post-sign-in destination
const BLOCKED_PATHS = ["/signup", "/signin", "/verify", "/onboarding"];

/**
 * Accept only same-origin relative paths ("/foo?bar#baz"). Anything that could
 * leave the site or run script (absolute URLs, "//host", "/\\host",
 * "javascript:") is rejected and returns null.
 */
export function sanitizeNextPath(rawNext: string | null | undefined): string | null {
  if (!rawNext) {
    return null;
  }

  const candidate = rawNext.trim();
  if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.startsWith("/\\")) {
    return null;
  }

  try {
    const parsed = new URL(candidate, "http://localhost");
    if (parsed.origin !== "http://localhost") {
      return null;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}

export function resolvePostAuthNextPath(rawNext: string | null | undefined): string | null {
  const safe = sanitizeNextPath(rawNext);
  if (!safe) {
    return null;
  }
  const pathname = new URL(safe, "http://localhost").pathname.toLowerCase().replace(/\/+$/, "");
  if (BLOCKED_PATHS.includes(pathname)) {
    return null;
  }
  return safe;
}

export function withNextParam(path: string, nextPath: string | null): string {
  if (!nextPath) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}next=${encodeURIComponent(nextPath)}`;
}
