import type { Cabinet, Library, ManagedLibrary, Song } from "$lib/types.js";

const API = "/api/connector";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiRequest<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(API + path, { ...init, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      // Keep the status-based message for non-JSON errors.
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const getCabinets = (token: string) => apiRequest<{ cabinets: Cabinet[] }>(token, "/cabinets");
export const getLibrary = (token: string) => apiRequest<Library>(token, "/library");
export const getManagedLibrary = (token: string) => apiRequest<ManagedLibrary>(token, "/library/manage");

export const uploadOsz = (token: string, file: File, category: string) => {
  const body = new FormData();
  body.append("category", category);
  body.append("file", file, file.name);
  return apiRequest<Song>(token, "/library/upload/osz", { method: "POST", body });
};

export const uploadTja = (token: string, files: File[], category: string) => {
  const body = new FormData();
  body.append("category", category);
  for (const file of files) body.append("files", file, file.webkitRelativePath || file.name);
  return apiRequest<Song>(token, "/library/upload/tja", { method: "POST", body });
};

export const deleteLibrarySong = (token: string, songId: string) =>
  apiRequest<{ status: string; song_id: string }>(token, `/library/songs/${songId}`, { method: "DELETE" });

export const deleteLibrarySongs = (token: string, songIds: string[]) =>
  apiRequest<{ status: string; deleted: string[]; missing: string[] }>(token, "/library/songs/delete-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_ids: songIds }),
  });

export const retryLibrarySong = (token: string, songId: string) =>
  apiRequest<{ status: string }>(token, `/library/songs/${songId}/retry`, { method: "POST" });

export const saveSelection = (token: string, cabinetId: string, songIds: string[]) =>
  apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/selection`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ song_ids: songIds }),
  });

export const saveConfig = (token: string, cabinetId: string, config: Record<string, string>) =>
  apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  });

export const deleteCabinet = (token: string, cabinetId: string) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}`, { method: "DELETE" });

export const resyncCabinet = (token: string, cabinetId: string) =>
  apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/resync`, { method: "POST" });
