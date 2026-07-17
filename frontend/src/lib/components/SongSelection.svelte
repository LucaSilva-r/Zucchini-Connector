<script lang="ts">
  import CheckCheckIcon from "@lucide/svelte/icons/check-check";
  import LoaderCircleIcon from "@lucide/svelte/icons/loader-circle";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { saveSelection } from "$lib/api.js";
  import * as Accordion from "$lib/components/ui/accordion/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Checkbox } from "$lib/components/ui/checkbox/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Switch } from "$lib/components/ui/switch/index.js";
  import type { Cabinet, Library, Song } from "$lib/types.js";

  let { token, cabinet, library, onSaved }: {
    token: string;
    cabinet: Cabinet;
    library: Library;
    onSaved: (cabinet: Cabinet) => void;
  } = $props();

  let query = $state("");
  let selectedOnly = $state(false);
  let draft = $state<string[]>([]);
  let draftCabinetId = $state("");
  let lastServerSelection = $state("");
  let dirty = $state(false);
  let saving = $state(false);
  let error = $state("");
  let openCategories = $state<string[]>([]);

  const draftSet = $derived(new Set(draft));
  const normalizedQuery = $derived(query.trim().toLocaleLowerCase());
  const visibleSongs = $derived.by(() => library.songs.filter((song) => {
    if (selectedOnly && !draftSet.has(song.id)) return false;
    if (!normalizedQuery) return true;
    return [song.title, song.display_title, song.subtitle, song.id]
      .some((value) => value?.toLocaleLowerCase().includes(normalizedQuery));
  }));

  $effect(() => {
    const ids = cabinet.managed
      ? (cabinet.queued_selection ?? cabinet.selection)
      : cabinet.have;
    const snapshot = ids.join("\n");
    if (draftCabinetId !== cabinet.cabinet_id || (!dirty && snapshot !== lastServerSelection)) {
      draft = [...ids];
      draftCabinetId = cabinet.cabinet_id;
      lastServerSelection = snapshot;
      dirty = false;
    }
  });

  function setSong(songId: string, checked: boolean) {
    draft = checked
      ? (draftSet.has(songId) ? draft : [...draft, songId])
      : draft.filter((id) => id !== songId);
    dirty = true;
  }

  function setSongs(songs: Song[], checked: boolean) {
    const ids = new Set(songs.map((song) => song.id));
    draft = checked
      ? [...new Set([...draft, ...ids])]
      : draft.filter((id) => !ids.has(id));
    dirty = true;
  }

  async function persistSelection() {
    saving = true;
    error = "";
    try {
      const updated = await saveSelection(token, cabinet.cabinet_id, draft);
      lastServerSelection = (updated.queued_selection ?? updated.selection).join("\n");
      dirty = false;
      onSaved(updated);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not save selection";
    } finally {
      saving = false;
    }
  }
</script>

<div class="grid gap-3">
  <div class="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
    <div class="relative min-w-0 flex-1 xl:max-w-xl">
      <SearchIcon class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input bind:value={query} class="pl-9" placeholder="Search title or song ID…" />
    </div>
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <Switch id="selected-only" bind:checked={selectedOnly} />
        <Label for="selected-only" class="font-normal">Selected only</Label>
      </div>
      <Badge variant="secondary">{draft.length} selected</Badge>
      <Button disabled={!dirty || saving} onclick={persistSelection}>
        {#if saving}<LoaderCircleIcon class="animate-spin" />{:else}<CheckCheckIcon />{/if}
        {saving ? "Saving" : "Save selection"}
      </Button>
    </div>
  </div>

  {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}
  {#if cabinet.queued_selection !== null}
    <p class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-300">
      This edit is queued. The cabinet will start it automatically after selection seq {cabinet.selection_seq} finishes.
    </p>
  {/if}

  <Accordion.Root type="multiple" bind:value={openCategories} class="rounded-lg border bg-background/60 px-3">
    {#each library.categories as category (category.id)}
      {@const songs = visibleSongs.filter((song) => song.category === category.id)}
      {@const selectedCount = library.songs.filter((song) => song.category === category.id && draftSet.has(song.id)).length}
      {#if songs.length > 0}
        <Accordion.Item value={category.id}>
          <Accordion.Trigger class="hover:no-underline">
            <span class="flex min-w-0 flex-1 items-center justify-between gap-3 pr-3 text-left">
              <span class="truncate font-medium">{category.title}</span>
              <span class="text-xs font-normal text-muted-foreground">{selectedCount}/{category.song_count}</span>
            </span>
          </Accordion.Trigger>
          <Accordion.Content class="grid gap-2 pb-3">
            <div class="flex items-center justify-between rounded-md bg-muted/60 px-2.5 py-1 text-xs text-muted-foreground">
              <span>{songs.length} visible songs</span>
              <div class="flex gap-1">
                <Button variant="ghost" size="sm" class="h-7" onclick={() => setSongs(songs, true)}>Select visible</Button>
                <Button variant="ghost" size="sm" class="h-7" onclick={() => setSongs(songs, false)}>Clear visible</Button>
              </div>
            </div>
            <div class="grid gap-0.5 sm:grid-cols-2 2xl:grid-cols-3">
              {#each songs as song (song.id)}
                <Label class="flex cursor-pointer items-center gap-2.5 rounded-md border border-transparent px-2 py-1 font-normal transition-colors hover:border-border hover:bg-accent/50">
                  <Checkbox checked={draftSet.has(song.id)} onCheckedChange={(value) => setSong(song.id, value === true)} />
                  <span class="min-w-0 truncate text-sm" title={song.id}>{song.display_title || song.title}</span>
                </Label>
              {/each}
            </div>
          </Accordion.Content>
        </Accordion.Item>
      {/if}
    {/each}
  </Accordion.Root>

  {#if visibleSongs.length === 0}
    <div class="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">No songs match this view.</div>
  {/if}
</div>
