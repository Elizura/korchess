/**
 * Game import client. Authentication required.
 */

import { withTrackingHeaders } from "@/lib/analytics/client";
import { API_BASE_URL } from "@/lib/api-url";

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
 * @param authToken Bearer token for authentication.
 * @param maxGames  Maximum number of games to import (default 250).
 */
export async function importGames(
  username: string,
  site: "lichess" | "chesscom",
  authToken: string,
  maxGames?: number
): Promise<ImportResponse> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${authToken}`,
  };

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
