/**
 * Unified game import client.
 *
 * Both anonymous and authenticated users go through POST /api/v1/import/{site}.
 * The backend accepts an optional Bearer token — authenticated users get their
 * analytics tied to their account, but auth is never required.
 */

import { withTrackingHeaders } from "@/lib/analytics/client";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://korchess.com";

export interface ImportResponse {
  username: string;
  imported: number;
  skipped: number;
  is_sync: boolean;
}

/**
 * Import (or sync) games for a given username and chess platform.
 *
 * @param username  Chess username on the given platform.
 * @param site      "lichess" or "chesscom".
 * @param authToken Optional Bearer token. Pass when the user is authenticated
 *                  so analytics events are attributed to their account.
 * @param maxGames  Maximum number of games to import (default 250).
 */
export async function importGames(
  username: string,
  site: "lichess" | "chesscom",
  authToken?: string,
  maxGames?: number
): Promise<ImportResponse> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const body: Record<string, unknown> = { username };
  if (maxGames !== undefined) {
    body["max_games"] = maxGames;
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/import/${site}`, {
    method: "POST",
    headers: withTrackingHeaders(headers),
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Import failed: ${response.status}`);
  }

  return response.json();
}
