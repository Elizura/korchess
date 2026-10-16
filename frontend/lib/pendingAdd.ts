/**
 * A chess account typed on the landing page, carried to the dashboard's
 * "Add chess account" field (as ?add=site:username, or in sessionStorage
 * across the sign-up -> verify -> onboarding hops). Only pre-fills; the user
 * still clicks Add.
 */
export type PendingAddSite = "lichess" | "chesscom";

export interface PendingAdd {
  site: PendingAddSite;
  username: string;
}

const STORAGE_KEY = "korchess_pending_add";
const USERNAME_PATTERN = /^[A-Za-z0-9_-]{2,30}$/;

export function isValidChessUsername(username: string): boolean {
  return USERNAME_PATTERN.test(username);
}

export function formatPendingAdd({ site, username }: PendingAdd): string {
  return `${site}:${username}`;
}

export function parsePendingAdd(raw: string | null | undefined): PendingAdd | null {
  if (!raw) return null;
  const separator = raw.indexOf(":");
  if (separator < 0) return null;
  const site = raw.slice(0, separator);
  const username = raw.slice(separator + 1);
  if (site !== "lichess" && site !== "chesscom") return null;
  if (!isValidChessUsername(username)) return null;
  return { site, username };
}

export function savePendingAdd(pending: PendingAdd): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, formatPendingAdd(pending));
  } catch {
    // Storage blocked (privacy mode, extensions): the hand-off is a nicety
  }
}

export function clearPendingAdd(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

/** Read and forget the stored account, if any */
export function takePendingAdd(): PendingAdd | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    window.sessionStorage.removeItem(STORAGE_KEY);
    return parsePendingAdd(raw);
  } catch {
    return null;
  }
}
