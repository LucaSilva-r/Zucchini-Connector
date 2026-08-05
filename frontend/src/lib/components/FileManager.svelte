<script lang="ts">
  import DownloadIcon from "@lucide/svelte/icons/download";
  import FileIcon from "@lucide/svelte/icons/file";
  import FolderIcon from "@lucide/svelte/icons/folder";
  import HardDriveIcon from "@lucide/svelte/icons/hard-drive";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import UploadIcon from "@lucide/svelte/icons/upload";
  import {
    downloadConsoleFile,
    listConsoleDirectory,
    pushConsoleFile,
    type ConsoleEntry,
    type ConsoleListing,
    type PushKind,
  } from "$lib/api.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import type { Cabinet } from "$lib/types.js";

  let { token, cabinet }: { token: string; cabinet: Cabinet } = $props();

  // Where an operator actually needs to look. / lists the mount points.
  const SHORTCUTS = ["/dev_hdd0/plugins/taiko", "/dev_hdd0/plugins", "/dev_hdd0/game", "/dev_hdd0/updater/01", "/dev_hdd0/tmp", "/"];

  const TARGETS: { kind: PushKind; label: string; path: string; accept: string; note: string; capability?: string }[] = [
    {
      kind: "mod",
      label: "Zucchini plugin",
      path: "/dev_hdd0/plugins/taiko/zucchini.sprx",
      accept: ".sprx",
      note: "Read at launch, so the game has to be restarted to pick it up.",
    },
    {
      kind: "config",
      label: "Zucchini config",
      path: "/dev_hdd0/plugins/taiko/taiko_config.cfg",
      accept: ".cfg,.txt",
      note: "The game's config, not the agent's. Rewritten on schema migrations.",
    },
    {
      kind: "agent",
      label: "webMAN agent",
      path: "/dev_hdd0/plugins/zucchini_agent.sprx",
      accept: ".sprx",
      note: "The plugin doing this transfer. Takes effect on the next reboot.",
    },
    {
      kind: "firmware",
      label: "PS3 firmware",
      path: "/dev_hdd0/updater/01/PS3UPDAT.PUP",
      accept: ".PUP,.pup",
      note: "The PS3 updater performs firmware validation. Use the Connector origin through an SSH tunnel if Cloudflare rejects the 200+ MiB upload.",
      capability: "firmware01",
    },
  ];

  let path = $state("/dev_hdd0");
  let listing = $state<ConsoleListing | null>(null);
  let loading = $state(false);
  let busy = $state("");
  let error = $state("");
  let notice = $state("");

  const parent = $derived(path === "/" ? "" : path.slice(0, path.lastIndexOf("/")) || "/");

  async function browse(next: string) {
    loading = true;
    error = "";
    notice = "";
    try {
      listing = await listConsoleDirectory(token, cabinet.cabinet_id, next);
      path = listing.path || next;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      loading = false;
    }
  }

  function join(name: string) {
    return path === "/" ? `/${name}` : `${path}/${name}`;
  }

  async function download(entry: ConsoleEntry) {
    busy = entry.name;
    error = "";
    notice = "";
    try {
      await downloadConsoleFile(token, cabinet.cabinet_id, join(entry.name));
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      busy = "";
    }
  }

  async function push(kind: PushKind, input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file) return;
    busy = kind;
    error = "";
    notice = "";
    try {
      const result = await pushConsoleFile(token, cabinet.cabinet_id, kind, file);
      notice = `Installed ${file.name} (${formatSize(result.bytes)}) on the cabinet.`;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      busy = "";
      input.value = "";
    }
  }

  function formatSize(bytes: number) {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} KiB`;
    return `${bytes} B`;
  }

  $effect(() => {
    if (cabinet.agent_online && !listing && !loading) browse(path);
  });
</script>

<div class="grid gap-4">
  <div class="flex flex-wrap items-start justify-between gap-3 rounded-lg border bg-muted/30 p-3">
    <div>
      <div class="flex items-center gap-2 text-sm font-semibold">
        <HardDriveIcon class="size-4" /> Console files
      </div>
      <p class="mt-1 max-w-3xl text-xs text-muted-foreground">
        Served by the VSH agent over its WSS or fallback poll, so it works behind NAT and while no game
        is running. Listing and downloading hold the request open until the console answers.
      </p>
    </div>
    <div class="flex items-center gap-2">
      <Badge variant={cabinet.agent_online ? "outline" : "secondary"}>
        VSH agent {cabinet.agent_online ? "online" : "offline"}
      </Badge>
      <Button variant="outline" size="sm" disabled={!cabinet.agent_online || loading} onclick={() => browse(path)}>
        <RefreshCwIcon class={loading ? "animate-spin" : ""} /> Refresh
      </Button>
    </div>
  </div>

  {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}
  {#if notice}<p class="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">{notice}</p>{/if}

  <section class="grid gap-3 rounded-lg border p-3">
    <div class="flex flex-wrap gap-1.5">
      {#each SHORTCUTS as shortcut (shortcut)}
        <Button variant="outline" size="sm" class="h-7 font-mono text-[11px]" disabled={!cabinet.agent_online || loading} onclick={() => browse(shortcut)}>
          {shortcut}
        </Button>
      {/each}
    </div>

    <form class="flex gap-2" onsubmit={(event) => { event.preventDefault(); browse(path); }}>
      <input
        class="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 font-mono text-sm"
        value={path}
        oninput={(event) => (path = event.currentTarget.value)}
        aria-label="Console path"
      />
      <Button type="submit" variant="outline" disabled={!cabinet.agent_online || loading}>Go</Button>
    </form>

    <div class="overflow-hidden rounded-md border">
      {#if !cabinet.agent_online}
        <p class="p-6 text-center text-sm text-muted-foreground">The cabinet's VSH agent is offline.</p>
      {:else if loading && !listing}
        <p class="p-6 text-center text-sm text-muted-foreground">Asking the console…</p>
      {:else if listing}
        <ul class="divide-y">
          {#if parent}
            <li>
              <button class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50" onclick={() => browse(parent)}>
                <FolderIcon class="size-4 shrink-0 text-muted-foreground" />
                <span class="font-mono">..</span>
              </button>
            </li>
          {/if}
          {#each listing.entries as entry (entry.name)}
            <li class="flex items-center gap-2 px-3 py-2 text-sm hover:bg-muted/50">
              {#if entry.directory}
                <button class="flex min-w-0 flex-1 items-center gap-2 text-left" onclick={() => browse(join(entry.name))}>
                  <FolderIcon class="size-4 shrink-0 text-sky-600 dark:text-sky-400" />
                  <span class="truncate font-mono">{entry.name}</span>
                </button>
              {:else}
                <FileIcon class="size-4 shrink-0 text-muted-foreground" />
                <span class="min-w-0 flex-1 truncate font-mono">{entry.name}</span>
                <span class="shrink-0 text-xs text-muted-foreground">{formatSize(entry.size)}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  class="h-7 shrink-0"
                  disabled={busy !== ""}
                  onclick={() => download(entry)}
                >
                  <DownloadIcon /> {busy === entry.name ? "Pulling…" : "Download"}
                </Button>
              {/if}
            </li>
          {/each}
          {#if !listing.entries.length}
            <li class="px-3 py-6 text-center text-sm text-muted-foreground">Empty directory.</li>
          {/if}
        </ul>
        {#if listing.truncated}
          <p class="border-t bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
            Too many entries to report — this listing is incomplete.
          </p>
        {/if}
      {/if}
    </div>
  </section>

  <section class="grid gap-3 rounded-lg border p-3">
    <div>
      <h3 class="text-sm font-semibold">Replace a file</h3>
      <p class="mt-0.5 text-xs text-muted-foreground">
        These destinations are fixed in the agent — the connector sends the file, never the path.
        The agent's own config is deliberately not replaceable: it holds the address and token this
        link runs on, so a bad push there could not be undone remotely.
      </p>
    </div>
    <div class="grid gap-2">
      {#each TARGETS as target (target.kind)}
        <div class="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-card p-2.5">
          <div class="min-w-0">
            <p class="text-sm font-medium">{target.label}</p>
            <p class="truncate font-mono text-[11px] text-muted-foreground">{target.path}</p>
            <p class="text-[11px] text-muted-foreground">{target.note}</p>
          </div>
          <label class="shrink-0">
            <input
              class="hidden"
              type="file"
              accept={target.accept}
              disabled={!cabinet.agent_online || busy !== "" || !!target.capability && !cabinet.agent_capabilities.includes(target.capability)}
              onchange={(event) => push(target.kind, event.currentTarget)}
            />
            <span
              class="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-muted aria-disabled:pointer-events-none aria-disabled:opacity-50"
              aria-disabled={!cabinet.agent_online || busy !== "" || !!target.capability && !cabinet.agent_capabilities.includes(target.capability)}
            >
              <UploadIcon class="size-4" /> {busy === target.kind ? "Sending…" : "Upload"}
            </span>
          </label>
        </div>
      {/each}
    </div>
  </section>
</div>
