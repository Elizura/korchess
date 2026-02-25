const BLOCKED_PREFIXES = ["/signup", "/onboarding"];

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
  const lower = safe.toLowerCase();
  if (BLOCKED_PREFIXES.some((prefix) => lower === prefix || lower.startsWith(`${prefix}?`))) {
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
