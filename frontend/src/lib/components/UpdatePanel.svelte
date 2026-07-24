<script lang="ts">
  import DownloadIcon from "@lucide/svelte/icons/download";
  import UploadIcon from "@lucide/svelte/icons/upload";
  import XIcon from "@lucide/svelte/icons/x";
  import RotateCcwIcon from "@lucide/svelte/icons/rotate-ccw";
  import { onMount } from "svelte";
  import { cancelZucchiniUpdate, getZucchiniUpdates, pushZucchiniUpdate, queueZucchiniUpdate } from "$lib/api.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Textarea } from "$lib/components/ui/textarea/index.js";
  import type { Cabinet, ZucchiniUpdate } from "$lib/types.js";

  let { token, cabinet, onSaved }: {
    token: string;
    cabinet: Cabinet;
    onSaved: (cabinet: Cabinet) => void;
  } = $props();

  let file = $state<File | null>(null);
  let version = $state("");
  let note = $state("");
  let busy = $state(false);
  let error = $state("");
  let message = $state("");
  let history = $state<ZucchiniUpdate[]>([]);
  let historyLoading = $state(true);

  const percent = $derived(cabinet.update_total > 0
    ? Math.min(100, Math.round(cabinet.update_done * 100 / cabinet.update_total))
    : 0);
  const canCancel = $derived(cabinet.update_pending !== null &&
    (!cabinet.update_dispatched || cabinet.update_phase === "failed"));

  async function pushUpdate() {
    if (!file || !version.trim()) return;
    busy = true;
    error = "";
    message = "";
    try {
      onSaved(await pushZucchiniUpdate(token, cabinet.cabinet_id, file, version.trim(), note.trim()));
      file = null;
      note = "";
      history = (await getZucchiniUpdates(token)).updates;
      message = "Update queued. The cabinet will install it from the attract screen and restart.";
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not queue the update";
    } finally {
      busy = false;
    }
  }

  async function cancelUpdate() {
    busy = true;
    error = "";
    try {
      onSaved(await cancelZucchiniUpdate(token, cabinet.cabinet_id));
      message = "Queued update cancelled.";
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not cancel the update";
    } finally {
      busy = false;
    }
  }

  async function installStored(update: ZucchiniUpdate) {
    busy = true;
    error = "";
    message = "";
    try {
      onSaved(await queueZucchiniUpdate(token, cabinet.cabinet_id, update.id));
      message = `${update.version} queued for installation.`;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not queue the stored update";
    } finally {
      busy = false;
    }
  }

  onMount(async () => {
    try {
      history = (await getZucchiniUpdates(token)).updates;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not load update history";
    } finally {
      historyLoading = false;
    }
  });
</script>

<div class="grid gap-4">
  <div class="rounded-lg border bg-muted/25 p-4">
    <div class="mb-3">
      <h3 class="font-medium">Push zucchini.sprx</h3>
      <p class="mt-1 text-xs text-muted-foreground">
        This cabinet reports {cabinet.flavor ? cabinet.flavor.toUpperCase() : "an unknown signing flavor"}.
        The connector verifies the SELF signature flavor and SHA-1 before the cabinet atomically installs it.
      </p>
    </div>
    <div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px_auto] md:items-end">
      <div class="grid gap-1.5">
        <Label for="zucchini-file">SPRX file</Label>
        <Input id="zucchini-file" type="file" accept=".sprx" onchange={(event) => file = event.currentTarget.files?.[0] ?? null} />
      </div>
      <div class="grid gap-1.5">
        <Label for="zucchini-version">Version</Label>
        <Input id="zucchini-version" placeholder="0.11.0" bind:value={version} />
      </div>
      <Button disabled={busy || !file || !version.trim()} onclick={pushUpdate}>
        <UploadIcon /> {busy ? "Queuing…" : "Push update"}
      </Button>
    </div>
    <div class="mt-3 grid gap-1.5">
      <Label for="zucchini-note">Change note</Label>
      <Textarea id="zucchini-note" rows={2} maxlength={500} placeholder="What changed in this build?" bind:value={note} />
    </div>
    {#if error}<p class="mt-3 text-sm text-destructive">{error}</p>{/if}
    {#if message}<p class="mt-3 text-sm text-emerald-700 dark:text-emerald-400">{message}</p>{/if}
  </div>

  {#if cabinet.update_pending || cabinet.update_phase !== "idle"}
    <div class="rounded-lg border border-sky-500/25 bg-sky-500/5 p-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p class="flex items-center gap-2 font-medium"><DownloadIcon class="size-4 text-sky-600" /> Remote update</p>
          <p class="mt-1 font-mono text-xs text-muted-foreground">
            {cabinet.update_pending?.version || cabinet.update_installed_version || "—"} · {cabinet.update_phase || "queued"}
          </p>
        </div>
        {#if canCancel}
          <Button variant="outline" size="sm" disabled={busy} onclick={cancelUpdate}><XIcon /> Cancel</Button>
        {/if}
      </div>
      {#if cabinet.update_total > 0}
        <div class="mt-3 h-2 overflow-hidden rounded-full bg-muted">
          <div class="h-full rounded-full bg-sky-500 transition-[width] duration-300" style={`width: ${percent}%`}></div>
        </div>
        <p class="mt-1.5 text-xs text-muted-foreground">{cabinet.update_done.toLocaleString()} / {cabinet.update_total.toLocaleString()} bytes</p>
      {/if}
      {#if cabinet.update_error}<p class="mt-2 text-sm text-destructive">{cabinet.update_error}</p>{/if}
    </div>
  {/if}

  <div class="rounded-lg border p-4">
    <div class="mb-3">
      <h3 class="font-medium">Stored versions</h3>
      <p class="mt-1 text-xs text-muted-foreground">Install an earlier compatible build to roll this cabinet back.</p>
    </div>
    {#if historyLoading}
      <p class="text-sm text-muted-foreground">Loading update history…</p>
    {:else if history.length === 0}
      <p class="text-sm text-muted-foreground">No builds stored yet. Pushing the first update adds it here.</p>
    {:else}
      <div class="grid gap-2">
        {#each history as update (update.id)}
          <div class="flex flex-col gap-3 rounded-md border bg-muted/20 p-3 sm:flex-row sm:items-start sm:justify-between">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium">{update.version}</span>
                <span class="rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase">{update.flavor}</span>
                {#if cabinet.update_installed_id === update.id}<span class="text-xs text-emerald-700 dark:text-emerald-400">Installed</span>{/if}
              </div>
              <p class="mt-1 text-sm text-muted-foreground">{update.note || "No change note"}</p>
              <p class="mt-1 font-mono text-[10px] text-muted-foreground">{update.id.slice(0, 12)} · {update.size.toLocaleString()} bytes · {new Date(update.uploaded_at * 1000).toLocaleString()}</p>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || cabinet.update_pending !== null || update.flavor !== cabinet.flavor || cabinet.update_installed_id === update.id}
              onclick={() => installStored(update)}
            >
              <RotateCcwIcon /> {cabinet.update_installed_id === update.id ? "Installed" : "Install"}
            </Button>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
