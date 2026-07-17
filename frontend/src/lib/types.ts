export type Cabinet = {
  cabinet_id: string;
  serial: string;
  name: string;
  game: string;
  game_name: string;
  version: string;
  last_seen: number;
  have: string[];
  reported_cfg: string;
  managed: boolean;
  selection: string[];
  queued_selection: string[] | null;
  selection_seq: number;
  acked_seq: number;
  operation_seq: number;
  operation_phase: string;
  operation_done: number;
  operation_total: number;
  operation_failed: number;
  operation_song: string;
  operation_error: string;
  config_pending: Record<string, string>;
};

export type SongCategory = { id: string; title: string; song_count: number };

export type Song = {
  id: string;
  title: string;
  display_title?: string;
  subtitle?: string;
  category: string;
  source?: string;
  rev?: string;
};

export type Library = {
  hash?: string;
  categories: SongCategory[];
  songs: Song[];
};

export type ManagedSong = Song & {
  source: "tja" | "osu";
  conversion_status: "ready" | "failed" | "queued" | "processing" | "unconverted" | "not_found";
  conversion_error: string;
  conversion_updated_at: string;
};

export type ManagedLibrary = {
  categories: { id: string; title: string }[];
  songs: ManagedSong[];
};
