<script lang="ts">
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import Clock3Icon from "@lucide/svelte/icons/clock-3";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import RadioIcon from "@lucide/svelte/icons/radio";
  import Trash2Icon from "@lucide/svelte/icons/trash-2";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import { deleteCabinet, resyncCabinet } from "$lib/api.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import * as Tabs from "$lib/components/ui/tabs/index.js";
  import ConfigPanel from "$lib/components/ConfigPanel.svelte";
  import ManagementGate from "$lib/components/ManagementGate.svelte";
  import RemoteControl from "$lib/components/RemoteControl.svelte";
  import SongSelection from "$lib/components/SongSelection.svelte";
  import UpdatePanel from "$lib/components/UpdatePanel.svelte";
  import { ensureManagement } from "$lib/management.svelte.js";
  import type { Cabinet, Library } from "$lib/types.js";

  let { token, cabinet, library, onUpdated, onDeleted }: {
    token: string;
    cabinet: Cabinet;
    library: Library;
    onUpdated: (cabinet: Cabinet) => void;
    onDeleted: (id: string) => void;
  } = $props();

  let deleting = $state(false);
  let forgetOpen = $state(false);
  let tab = $state("songs");
  let deleteError = $state("");
  let resyncing = $state(false);
  const liveCabinetId = $derived(cabinet.cabinet_id);

  function eventUrl(id: string) {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${location.host}/api/ui/cabinets/${encodeURIComponent(id)}/events`;
  }

  $effect(() => {
    const cabinetId = liveCabinetId;
    let stopped = false;
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    const connect = () => {
      if (stopped) return;
      const ws = new WebSocket(eventUrl(cabinetId));
      socket = ws;
      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(String(event.data));
          if (message.type === "status" && message.cabinet)
            onUpdated(message.cabinet as Cabinet);
        } catch {
          // The normal ten-second refresh remains the recovery path.
        }
      };
      ws.onclose = () => {
        if (socket === ws) socket = null;
        if (!stopped) retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      socket?.close(1000, "Dashboard closed");
    };
  });

  async function forceResync() {
    if (!(await ensureManagement())) return;
    resyncing = true;
    try {
      onUpdated(await resyncCabinet(token, cabinet.cabinet_id));
    } catch {
      // Transient; the pending state shows on the next poll either way.
    } finally {
      resyncing = false;
    }
  }
  const online = $derived(cabinet.control_online || Date.now() / 1000 - cabinet.last_seen <= 30);
  const pending = $derived(Object.keys(cabinet.config_pending).length > 0 || cabinet.active_seq < cabinet.selection_seq || cabinet.queued_selection !== null || cabinet.update_pending !== null);
  const desiredCount = $derived((cabinet.queued_selection ?? cabinet.selection).length);
  const stalePackages = $derived(Object.values(cabinet.package_states).filter((pkg) => pkg.state === "stale").length);
  const blockedPackages = $derived(Object.values(cabinet.package_states).filter((pkg) => pkg.state === "blocked").length);
  const operationVisible = $derived(
    cabinet.active_seq < cabinet.selection_seq ||
    !["idle", "complete", "complete_errors"].includes(cabinet.operation_phase)
  );
  const operationPercent = $derived(cabinet.operation_total > 0
    ? Math.min(100, Math.round(cabinet.operation_done * 100 / cabinet.operation_total))
    : 0);
  const transferPercent = $derived(cabinet.transfer_total > 0
    ? Math.min(100, Math.round(cabinet.transfer_done * 100 / cabinet.transfer_total))
    : 0);
  const transferSpeed = $derived(cabinet.transfer_bps >= 1024 * 1024
    ? `${(cabinet.transfer_bps / (1024 * 1024)).toFixed(1)} MiB/s`
    : `${Math.round(cabinet.transfer_bps / 1024)} KiB/s`);
  const missingSongs = $derived.by(() => {
    if (!cabinet.managed) return [];
    const have = new Set(cabinet.have);
    const titles = new Map(library.songs.map((song) => [song.id, song.display_title || song.title]));
    return (cabinet.queued_selection ?? cabinet.selection)
      .filter((id) => !have.has(id))
      .map((id) => ({ id, title: titles.get(id) }));
  });

  // PIN first, then the confirmation.
  async function requestForget() {
    if (await ensureManagement()) forgetOpen = true;
  }

  async function forgetCabinet() {
    deleting = true;
    deleteError = "";
    try {
      await deleteCabinet(token, cabinet.cabinet_id);
      onDeleted(cabinet.cabinet_id);
    } catch (reason) {
      deleteError = reason instanceof Error ? reason.message : "Could not forget cabinet";
      deleting = false;
    }
  }
</script>

<Card.Root class="operator-panel min-w-0" size="sm">
  <Card.Header class="gap-3 border-b">
    <div class="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
      <div class="min-w-0">
        <div class="mb-1.5 flex flex-wrap items-center gap-1.5">
          <Badge variant={online ? "default" : "secondary"} class={online ? "bg-emerald-600 hover:bg-emerald-600" : ""}>
            <RadioIcon /> {online ? "Online" : "Offline"}
          </Badge>
          <Badge variant="outline">{cabinet.game_name || cabinet.game || "Unknown build"}</Badge>
          {#if !cabinet.managed}<Badge variant="outline">Unmanaged</Badge>{/if}
          {#if cabinet.queued_selection !== null}<Badge variant="outline" class="text-amber-700 dark:text-amber-400">Next selection queued</Badge>{/if}
          {#if pending}<Badge variant="outline" class="text-amber-700 dark:text-amber-400"><Clock3Icon /> Sync pending</Badge>{:else}<Badge variant="outline" class="text-emerald-700 dark:text-emerald-400"><CircleCheckIcon /> Synced</Badge>{/if}
        </div>
        <Card.Title class="truncate text-lg">{cabinet.name || "Unnamed cabinet"}</Card.Title>
        <Card.Description class="mt-0.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs break-all">
          <span>ID {cabinet.cabinet_id}</span><span>Serial {cabinet.serial || "—"}</span><span>Zucchini {cabinet.version || "—"}</span>
        </Card.Description>
      </div>

      <div class="shrink-0">
        <Button variant="outline" size="sm" onclick={requestForget}><Trash2Icon /> Forget cabinet</Button>
        <AlertDialog.Root bind:open={forgetOpen}>
          <AlertDialog.Content>
            <AlertDialog.Header>
              <AlertDialog.Title>Forget {cabinet.name || cabinet.cabinet_id}?</AlertDialog.Title>
              <AlertDialog.Description>
                Its saved selection and queued settings will be removed. The cabinet will appear again at its next poll.
              </AlertDialog.Description>
            </AlertDialog.Header>
            {#if deleteError}<p class="text-sm text-destructive">{deleteError}</p>{/if}
            <AlertDialog.Footer>
              <AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
              <AlertDialog.Action variant="destructive" disabled={deleting} onclick={forgetCabinet}>
                {deleting ? "Forgetting…" : "Forget cabinet"}
              </AlertDialog.Action>
            </AlertDialog.Footer>
          </AlertDialog.Content>
        </AlertDialog.Root>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{cabinet.have.length}</span><span class="truncate text-xs text-muted-foreground">cached</span></div>
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{cabinet.managed ? desiredCount : "—"}</span><span class="truncate text-xs text-muted-foreground">desired</span></div>
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{stalePackages + blockedPackages}</span><span class="truncate text-xs text-muted-foreground">{blockedPackages ? `${blockedPackages} blocked` : "stale"}</span></div>
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{Object.keys(cabinet.config_pending).length}</span><span class="truncate text-xs text-muted-foreground">pending config</span></div>
    </div>

    {#if operationVisible}
      <div class="rounded-lg border border-amber-500/25 bg-amber-500/5 p-3">
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
          <span class="flex items-center gap-2 font-medium"><DownloadIcon class="size-4 text-amber-600" /> Background song sync</span>
          <span class="font-mono text-xs text-muted-foreground">seq {cabinet.operation_seq || cabinet.selection_seq} · {cabinet.operation_phase || "queued"}</span>
        </div>
        <div class="h-2 overflow-hidden rounded-full bg-muted">
          <div class="h-full rounded-full bg-amber-500 transition-[width] duration-300" style={`width: ${operationPercent}%`}></div>
        </div>
        <div class="mt-2 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span>{cabinet.operation_done}/{cabinet.operation_total || desiredCount} ready{cabinet.operation_failed ? ` · ${cabinet.operation_failed} failed` : ""}</span>
          {#if cabinet.operation_song}<span class="truncate font-mono">{cabinet.operation_song}</span>{/if}
        </div>
        {#if cabinet.transfer_active}
          <div class="mt-3 border-t border-amber-500/20 pt-2">
            <div class="mb-1.5 flex justify-between gap-3 text-xs">
              <span class="truncate font-mono">{cabinet.transfer_asset || "Downloading asset"}</span>
              <span class="shrink-0 text-muted-foreground">{transferPercent}% · {transferSpeed}</span>
            </div>
            <div class="h-1.5 overflow-hidden rounded-full bg-muted">
              <div class="h-full rounded-full bg-sky-500 transition-[width] duration-200" style={`width: ${transferPercent}%`}></div>
            </div>
          </div>
        {/if}
        {#if cabinet.operation_error}<p class="mt-2 text-xs text-destructive">{cabinet.operation_error}</p>{/if}
      </div>
    {/if}

    {#if !operationVisible && missingSongs.length}
      <div class="rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="font-medium">{missingSongs.length} selected {missingSongs.length === 1 ? "song is" : "songs are"} not on the cabinet</p>
          <Button variant="outline" size="sm" class="h-7" disabled={resyncing} onclick={forceResync}><RefreshCwIcon class={resyncing ? "animate-spin" : ""} /> Force resync</Button>
        </div>
        <ul class="mt-1.5 grid max-h-40 gap-0.5 overflow-y-auto text-xs text-muted-foreground">
          {#each missingSongs as song (song.id)}
            <li class="flex min-w-0 items-baseline gap-2"><span class="truncate">{song.title ?? "Unavailable song"}</span><span class="truncate font-mono text-[10px]">{song.id}</span></li>
          {/each}
        </ul>
        <p class="mt-1.5 text-xs text-muted-foreground">The cabinet failed to download or verify these during its last sync. Use Force resync to retry them.</p>
      </div>
    {/if}
  </Card.Header>

  <Card.Content class="p-3 sm:p-4">
    <Tabs.Root bind:value={tab}>
      <Tabs.List class="mb-3 max-w-full">
        <Tabs.Trigger value="songs">Library</Tabs.Trigger>
        <Tabs.Trigger value="control">Control</Tabs.Trigger>
        <Tabs.Trigger value="config">Config</Tabs.Trigger>
        <Tabs.Trigger value="update">Update</Tabs.Trigger>
      </Tabs.List>
      <!-- bits-ui renders inactive tab panels too, so each one is mounted
           only while selected: the controller must not hold a socket (or the
           keyboard) from behind the song list. -->
      <Tabs.Content value="songs">{#if tab === "songs"}<SongSelection {token} {cabinet} {library} onSaved={onUpdated} />{/if}</Tabs.Content>
      <Tabs.Content value="control">{#if tab === "control"}<ManagementGate><RemoteControl {token} {cabinet} /></ManagementGate>{/if}</Tabs.Content>
      <Tabs.Content value="config">{#if tab === "config"}<ManagementGate><ConfigPanel {token} {cabinet} onSaved={onUpdated} /></ManagementGate>{/if}</Tabs.Content>
      <Tabs.Content value="update">{#if tab === "update"}<ManagementGate><UpdatePanel {token} {cabinet} onSaved={onUpdated} /></ManagementGate>{/if}</Tabs.Content>
    </Tabs.Root>
  </Card.Content>
</Card.Root>
