import type { Cabinet, Library, ManagedLibrary, Song, ZucchiniUpdate } from "$lib/types.js";

const API = "/api/ui";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiRequest<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(API + path, { ...init, headers, credentials: "same-origin" });
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

export type ManagementStatus = { configured: boolean; unlocked: boolean };
export const getManagementStatus = () => apiRequest<ManagementStatus>("", "/auth/status");
export const unlockManagement = (pin: string) =>
  apiRequest<ManagementStatus>("", "/auth/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin }),
  });
export const lockManagement = () =>
  apiRequest<ManagementStatus>("", "/auth/logout", { method: "POST" });

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

export type ConvertAllResult = {
  status: string;
  accepted: number;
  scheduled: number;
  already_scheduled: number;
  not_found: number;
};

export const convertLibrary = (token: string, includeFailed: boolean) =>
  apiRequest<ConvertAllResult>(token, `/library/convert-all?include_failed=${includeFailed}`, {
    method: "POST",
  });

export const reconvertLibrarySongs = (token: string, songIds: string[]) =>
  apiRequest<{ status: string; requested: number; scheduled: number; not_found: number }>(
    token,
    "/library/songs/reconvert-batch",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ song_ids: songIds }),
    },
  );

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

export const readItaikoSettings = (token: string, cabinetId: string, index: number) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}/itaiko/${index}/read`, {
    method: "POST",
  });

export const saveItaikoSettings = (
  token: string,
  cabinetId: string,
  index: number,
  itaikoSettings: Record<string, number>,
) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}/itaiko/${index}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ itaiko_settings: itaikoSettings }),
  });

export const deleteCabinet = (token: string, cabinetId: string) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}`, { method: "DELETE" });

export const exitCabinetGame = (token: string, cabinetId: string) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}/exit`, { method: "POST" });

/** The three that end a play in progress; each one is confirmed in the UI. */
export type DangerAction = "restart_game" | "exit_game" | "reboot";
/** Virtual-pad buttons. Must match PAD_BUTTONS in app/main.py, lowercased. */
export type PadButton =
  | "up" | "down" | "left" | "right"
  | "cross" | "circle" | "square" | "triangle"
  | "l1" | "l2" | "r1" | "r2" | "l3" | "r3"
  | "select" | "start" | "psbtn"
  | "off";
export type WebmanAction = DangerAction | `pad_${PadButton}`;

export const runWebmanAction = (token: string, cabinetId: string, action: WebmanAction) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}/webman`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });

export const pressPadButton = (token: string, cabinetId: string, button: PadButton) =>
  runWebmanAction(token, cabinetId, `pad_${button}`);

export type DebugTunnelStatus = {
  enabled: boolean;
  available?: boolean;
  cabinet_id: string;
  client_connected: boolean;
  host: string;
  port: number;
};

export const getDebugTunnel = (token: string, cabinetId: string) =>
  apiRequest<DebugTunnelStatus>(token, `/cabinets/${cabinetId}/debug`);

export const setDebugTunnel = (token: string, cabinetId: string, enabled: boolean) =>
  apiRequest<DebugTunnelStatus>(token, `/cabinets/${cabinetId}/debug`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });

export const requestScreenshot = (token: string, cabinetId: string) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}/screenshot`, { method: "POST" });

export const screenshotUrl = (cabinetId: string, stamp: number) =>
  `${API}/cabinets/${encodeURIComponent(cabinetId)}/screenshot?t=${stamp}`;

/** Ask for a capture and wait for the image to land, returning a cache-busting
 *  stamp for `screenshotUrl`. The console captures and uploads *after* it
 *  answers, so the file is polled rather than waited on for a guessed delay,
 *  and `X-Captured-At` is what tells a fresh capture from the last one. */
export async function captureScreenshot(token: string, cabinetId: string): Promise<number> {
  await requestScreenshot(token, cabinetId);
  for (let attempt = 0; attempt < 15; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const stamp = Date.now();
    const probe = await fetch(screenshotUrl(cabinetId, stamp), { credentials: "include" });
    if (probe.ok && Number(probe.headers.get("X-Captured-At") ?? 0) * 1000 > stamp - 60000) return stamp;
  }
  throw new Error(
    "No screenshot arrived. In-game capture needs the cabinet's control socket; on XMB it needs the webMAN agent.",
  );
}

export const refreshInstalledGames = (token: string, cabinetId: string) =>
  apiRequest<{ status: string }>(token, `/cabinets/${cabinetId}/games/refresh`, {
    method: "POST",
  });

export const saveGameAutoboot = (
  token: string,
  cabinetId: string,
  directory: string,
  delay: number,
) =>
  apiRequest<{ status: string; directory: string; delay: number }>(
    token,
    `/cabinets/${cabinetId}/games/autoboot`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory, delay }),
    },
  );

export const launchInstalledGame = (token: string, cabinetId: string, directory: string) =>
  apiRequest<{ status: string; directory: string }>(token, `/cabinets/${cabinetId}/games/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ directory }),
  });

export type ConsoleEntry = { name: string; directory: boolean; size: number; mtime: number };
export type ConsoleListing = { path: string; entries: ConsoleEntry[]; error: boolean; truncated: boolean };

/** Browse one directory on the console. The request is held open until the
 *  cabinet answers over its poll, so it is slower than a normal call. */
export const listConsoleDirectory = (token: string, cabinetId: string, path: string) =>
  apiRequest<ConsoleListing>(token, `/cabinets/${cabinetId}/fs/list`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

/** Pull one file off the console and save it. The connector holds the request
 *  while the cabinet uploads, so the browser gets the bytes in one go. */
export async function downloadConsoleFile(token: string, cabinetId: string, path: string) {
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/ui/cabinets/${cabinetId}/fs/fetch`, {
    method: "POST",
    headers,
    credentials: "same-origin",
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    let detail = `Download failed (${response.status})`;
    try {
      detail = String((await response.json()).detail ?? detail);
    } catch {
      // Keep the status-based message.
    }
    throw new ApiError(response.status, detail);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = path.split("/").pop() || "download.bin";
  link.click();
  URL.revokeObjectURL(url);
}

/** The fixed files a cabinet will accept. Not a path: the destination lives
 *  in the agent's own table, and the agent's config is not in it. */
export type PushKind = "agent" | "mod" | "config" | "firmware";

export const pushConsoleFile = (token: string, cabinetId: string, kind: PushKind, file: File) => {
  const body = new FormData();
  body.append("kind", kind);
  body.append("file", file, file.name);
  return apiRequest<{ status: string; kind: string; bytes: number }>(
    token,
    `/cabinets/${cabinetId}/fs/push`,
    { method: "POST", body },
  );
};

export const resyncCabinet = (token: string, cabinetId: string) =>
  apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/resync`, { method: "POST" });

export const pushZucchiniUpdate = (token: string, cabinetId: string, file: File, version: string, note: string) => {
  const body = new FormData();
  body.append("file", file, file.name);
  body.append("version", version);
  body.append("note", note);
  return apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/update`, { method: "POST", body });
};

export const cancelZucchiniUpdate = (token: string, cabinetId: string) =>
  apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/update`, { method: "DELETE" });

export const getZucchiniUpdates = (token: string) =>
  apiRequest<{ updates: ZucchiniUpdate[] }>(token, "/updates");

export const queueZucchiniUpdate = (token: string, cabinetId: string, updateId: string) =>
  apiRequest<Cabinet>(token, `/cabinets/${cabinetId}/update/${updateId}`, { method: "POST" });
