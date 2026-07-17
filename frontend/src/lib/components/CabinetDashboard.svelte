<script lang="ts">
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import Clock3Icon from "@lucide/svelte/icons/clock-3";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import RadioIcon from "@lucide/svelte/icons/radio";
  import Trash2Icon from "@lucide/svelte/icons/trash-2";
  import { deleteCabinet } from "$lib/api.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { buttonVariants } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import * as Tabs from "$lib/components/ui/tabs/index.js";
  import ConfigPanel from "$lib/components/ConfigPanel.svelte";
  import SongSelection from "$lib/components/SongSelection.svelte";
  import type { Cabinet, Library } from "$lib/types.js";

  let { token, cabinet, library, onUpdated, onDeleted }: {
    token: string;
    cabinet: Cabinet;
    library: Library;
    onUpdated: (cabinet: Cabinet) => void;
    onDeleted: (id: string) => void;
  } = $props();

  let deleting = $state(false);
  let deleteError = $state("");
  const online = $derived(Date.now() / 1000 - cabinet.last_seen <= 30);
  const pending = $derived(Object.keys(cabinet.config_pending).length > 0 || cabinet.acked_seq < cabinet.selection_seq || cabinet.queued_selection !== null);
  const desiredCount = $derived((cabinet.queued_selection ?? cabinet.selection).length);
  const operationVisible = $derived(
    cabinet.acked_seq < cabinet.selection_seq ||
    !["idle", "complete"].includes(cabinet.operation_phase)
  );
  const operationPercent = $derived(cabinet.operation_total > 0
    ? Math.min(100, Math.round(cabinet.operation_done * 100 / cabinet.operation_total))
    : 0);

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
        <Card.Description class="mt-0.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs">
          <span>ID {cabinet.cabinet_id}</span><span>Serial {cabinet.serial || "—"}</span><span>Zucchini {cabinet.version || "—"}</span>
        </Card.Description>
      </div>

      <AlertDialog.Root>
        <AlertDialog.Trigger class={buttonVariants({ variant: "outline", size: "sm" })}>
          <Trash2Icon /> Forget cabinet
        </AlertDialog.Trigger>
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

    <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{cabinet.have.length}</span><span class="truncate text-xs text-muted-foreground">cached</span></div>
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{cabinet.managed ? desiredCount : "—"}</span><span class="truncate text-xs text-muted-foreground">desired</span></div>
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{Object.keys(cabinet.config_pending).length}</span><span class="truncate text-xs text-muted-foreground">pending config</span></div>
      <div class="flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5"><span class="text-sm font-semibold">{cabinet.acked_seq}/{cabinet.selection_seq}</span><span class="truncate text-xs text-muted-foreground">selection seq</span></div>
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
        {#if cabinet.operation_error}<p class="mt-2 text-xs text-destructive">{cabinet.operation_error}</p>{/if}
      </div>
    {/if}
  </Card.Header>

  <Card.Content class="p-3 sm:p-4">
    <Tabs.Root value="songs">
      <Tabs.List class="mb-3">
        <Tabs.Trigger value="songs">Song library</Tabs.Trigger>
        <Tabs.Trigger value="config">Configuration</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content value="songs"><SongSelection {token} {cabinet} {library} onSaved={onUpdated} /></Tabs.Content>
      <Tabs.Content value="config"><ConfigPanel {token} {cabinet} onSaved={onUpdated} /></Tabs.Content>
    </Tabs.Root>
  </Card.Content>
</Card.Root>
