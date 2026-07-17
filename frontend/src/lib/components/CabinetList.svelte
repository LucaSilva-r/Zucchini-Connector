<script lang="ts">
  import RadioIcon from "@lucide/svelte/icons/radio";
  import ServerIcon from "@lucide/svelte/icons/server";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import type { Cabinet } from "$lib/types.js";

  let { cabinets, selectedId, onSelect }: {
    cabinets: Cabinet[];
    selectedId: string | null;
    onSelect: (id: string) => void;
  } = $props();

  const isOnline = (cabinet: Cabinet) => Date.now() / 1000 - cabinet.last_seen <= 30;
  const isPending = (cabinet: Cabinet) =>
    Object.keys(cabinet.config_pending).length > 0 || cabinet.acked_seq < cabinet.selection_seq;

  function ago(epoch: number) {
    if (!epoch) return "Never seen";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    return `${Math.round(seconds / 3600)}h ago`;
  }
</script>

<Card.Root class="operator-panel h-fit lg:sticky lg:top-6" size="sm">
  <Card.Header class="gap-0.5 border-b">
    <div class="flex items-center justify-between gap-3">
      <Card.Title class="flex items-center gap-2 text-base">
        <ServerIcon class="size-4 text-primary" /> Cabinets
      </Card.Title>
      <Badge variant="secondary">{cabinets.length}</Badge>
    </div>
    <Card.Description>Select a cabinet to manage.</Card.Description>
  </Card.Header>
  <Card.Content class="p-2">
    {#if cabinets.length === 0}
      <div class="px-4 py-10 text-center text-sm text-muted-foreground">
        No cabinets have checked in yet.
      </div>
    {:else}
      <div class="grid gap-1">
        {#each cabinets as cabinet (cabinet.cabinet_id)}
          <button
            type="button"
            class="group grid w-full gap-1 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-accent/60 {selectedId === cabinet.cabinet_id ? 'border-primary/50 bg-accent' : 'border-transparent'}"
            onclick={() => onSelect(cabinet.cabinet_id)}
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="truncate text-sm font-semibold">{cabinet.name || "Unnamed cabinet"}</div>
                <div class="mt-0.5 truncate text-xs text-muted-foreground">
                  {cabinet.game_name || cabinet.game || "Unknown build"} · {cabinet.serial || "No serial"}
                </div>
              </div>
              <span class="mt-1 size-2 shrink-0 rounded-full {isOnline(cabinet) ? 'bg-emerald-500 shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-emerald-500)_18%,transparent)]' : 'bg-muted-foreground/40'}"></span>
            </div>
            <div class="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span class="flex items-center gap-1"><RadioIcon class="size-3" /> {ago(cabinet.last_seen)}</span>
              <span class="flex items-center gap-2">
                <span>{cabinet.have.length} songs</span>
                {#if isPending(cabinet)}<Badge variant="outline" class="h-5 text-[10px] text-amber-700 dark:text-amber-400">Pending</Badge>{/if}
              </span>
            </div>
          </button>
        {/each}
      </div>
    {/if}
  </Card.Content>
</Card.Root>
