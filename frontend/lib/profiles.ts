import type { ChessProfile } from "@/components/ChessProfileCard";
import { withTrackingHeaders } from "@/lib/analytics/client";
import { API_BASE_URL } from "@/lib/api-url";

export interface ImportResponse {
  username: string;
  imported: number;
  skipped: number;
  is_sync: boolean;
}

export interface ChessProfileWithImport {
  profile: ChessProfile;
  import_result: ImportResponse;
}

export interface ChessProfileSyncResponse {
  profile: ChessProfile;
  sync_result: ImportResponse;
}

export interface ChessProfileListResponse {
  profiles: ChessProfile[];
}

export async function fetchProfiles(
  authToken: string
): Promise<ChessProfile[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/profiles`, {
    method: "GET",
    headers: withTrackingHeaders({
      Authorization: `Bearer ${authToken}`,
    }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to fetch profiles: ${response.status}`);
  }

  const data: ChessProfileListResponse = await response.json();
  return data.profiles;
}

export async function addProfile(
  authToken: string,
  username: string,
  site: "lichess" | "chesscom"
): Promise<ChessProfileWithImport> {
  const response = await fetch(`${API_BASE_URL}/api/v1/profiles`, {
    method: "POST",
    headers: withTrackingHeaders({
      "Content-Type": "application/json",
      Authorization: `Bearer ${authToken}`,
    }),
    body: JSON.stringify({ username, site }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to add profile: ${response.status}`);
  }

  return response.json();
}

export async function importProfileGames(
  authToken: string,
  site: "lichess" | "chesscom",
  username: string
): Promise<ImportResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/profiles/${site}/${encodeURIComponent(username)}/import`,
    {
      method: "POST",
      headers: withTrackingHeaders({
        Authorization: `Bearer ${authToken}`,
      }),
    }
  );

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to import games: ${response.status}`);
  }

  return response.json();
}

export async function syncProfile(
  authToken: string,
  site: "lichess" | "chesscom",
  username: string
): Promise<ChessProfileSyncResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/profiles/${site}/${encodeURIComponent(username)}/sync`,
    {
      method: "POST",
      headers: withTrackingHeaders({
        Authorization: `Bearer ${authToken}`,
      }),
    }
  );

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to sync profile: ${response.status}`);
  }

  return response.json();
}

export async function deleteProfile(
  authToken: string,
  site: "lichess" | "chesscom",
  username: string
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/profiles/${site}/${encodeURIComponent(username)}`,
    {
      method: "DELETE",
      headers: withTrackingHeaders({
        Authorization: `Bearer ${authToken}`,
      }),
    }
  );

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to delete profile: ${response.status}`);
  }
}
