export type Cabinet = {
  cabinet_id: string;
  serial: string;
  name: string;
  game: string;
  game_name: string;
  build: string;
  version: string;
  song_inject: boolean;
  config_version: number;
  agent_ever: boolean;
  agent_online: boolean;
  agent_state: string;
  agent_seen: number;
  installed_games: InstalledGame[];
  games_updated_at: number;
  autoboot_dir: string;
  autoboot_delay: number;
  flavor: string;
  last_seen: number;
  have: string[];
  reported_cfg: string;
  managed: boolean;
  selection: string[];
  queued_selection: string[] | null;
  selection_seq: number;
  acked_seq: number;
  desired_ack: number;
  active_seq: number;
  verify_generation: number;
  verify_ack: number;
  package_states: Record<string, {
    revision: string;
    state: string;
    error_code: string;
  }>;
  operation_seq: number;
  operation_phase: string;
  operation_done: number;
  operation_total: number;
  operation_failed: number;
  operation_song: string;
  operation_error: string;
  transfer_active: boolean;
  transfer_asset: string;
  transfer_done: number;
  transfer_total: number;
  transfer_bps: number;
  config_pending: Record<string, string>;
  update_pending: {
    id: string;
    sha1: string;
    version: string;
    size: number;
    filename: string;
    flavor: string;
    note: string;
    uploaded_at: number;
  } | null;
  update_dispatched: boolean;
  update_installed_id: string;
  update_installed_version: string;
  update_phase: string;
  update_done: number;
  update_total: number;
  update_error: string;
  control_online: boolean;
  control_operator: boolean;
};

export type InstalledGame = {
  directory: string;
  title_id: string;
  title: string;
  version: string;
  has_icon: boolean;
};

export type ZucchiniUpdate = {
  id: string;
  sha1: string;
  version: string;
  size: number;
  filename: string;
  flavor: string;
  note: string;
  uploaded_at: number;
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
  conversion_status: "ready" | "failed" | "queued" | "processing" | "retrying" | "unconverted" | "not_found";
  conversion_error: string;
  conversion_updated_at: string;
};

export type ManagedLibrary = {
  categories: { id: string; title: string }[];
  songs: ManagedSong[];
};
