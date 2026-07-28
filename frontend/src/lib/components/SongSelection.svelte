<script lang="ts">
  import CheckCheckIcon from "@lucide/svelte/icons/check-check";
  import CircleAlertIcon from "@lucide/svelte/icons/circle-alert";
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

  // Rows are plain DOM but there are thousands of them, so a category only
  // renders once it is opened, and only up to this many songs at a time.
  const ROW_LIMIT = 300;

  let query = $state("");
  let appliedQuery = $state("");
  let sourceFilter = $state("all");
  let expanded = $state<string[]>([]);
  let selectedOnly = $state(false);
  let draft = $state<string[]>([]);
  let draftCabinetId = $state("");
  let lastServerSelection = $state("");
  let dirty = $state(false);
  let saving = $state(false);
  let error = $state("");
  let openCategories = $state<string[]>([]);

  const draftSet = $derived(new Set(draft));
  const normalizedQuery = $derived(appliedQuery.trim().toLocaleLowerCase());
  const visibleSongs = $derived.by(() => library.songs.filter((song) => {
    if (selectedOnly && !draftSet.has(song.id)) return false;
    if (sourceFilter !== "all" && (song.source ?? "tja") !== sourceFilter) return false;
    if (!normalizedQuery) return true;
    return [song.title, song.display_title, song.subtitle, song.id]
      .some((value) => value?.toLocaleLowerCase().includes(normalizedQuery));
  }));
  // One pass instead of re-filtering the whole library per category.
  const songsByCategory = $derived.by(() => {
    const buckets = new Map<string, Song[]>();
    for (const song of visibleSongs) {
      const bucket = buckets.get(song.category);
      if (bucket) bucket.push(song);
      else buckets.set(song.category, [song]);
    }
    return buckets;
  });
  const selectedByCategory = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const song of library.songs) {
      if (draftSet.has(song.id)) counts.set(song.category, (counts.get(song.category) ?? 0) + 1);
    }
    return counts;
  });

  // A new filter means a new list, so go back to the capped view.
  $effect(() => {
    normalizedQuery;
    sourceFilter;
    selectedOnly;
    expanded = [];
  });

  // Typing re-filters thousands of songs, so apply the query once the operator
  // pauses rather than on every keystroke.
  $effect(() => {
    const next = query;
    if (next === appliedQuery) return;
    const timer = setTimeout(() => appliedQuery = next, 200);
    return () => clearTimeout(timer);
  });

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
      <select bind:value={sourceFilter} aria-label="Chart source" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">All sources</option>
        <option value="tja">TJA only</option>
        <option value="osu">osu! only</option>
      </select>
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
  {#if cabinet.game && !cabinet.song_inject}
    <p class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-300">
      <CircleAlertIcon class="mr-1 inline size-4 align-text-bottom" />
      This cabinet runs {cabinet.game_name || cabinet.game}, where custom song injection is not supported. Songs you select are still
      downloaded and installed, but they will not appear in song select. Only Blue and Green builds show them today.
    </p>
  {/if}
  {#if cabinet.queued_selection !== null}
    <p class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-300">
      This edit is queued. The cabinet will start it automatically after selection seq {cabinet.selection_seq} finishes.
    </p>
  {/if}

  <Accordion.Root type="multiple" bind:value={openCategories} class="rounded-lg border bg-background/60 px-3">
    {#each library.categories as category (category.id)}
      {@const songs = songsByCategory.get(category.id) ?? []}
      {@const selectedCount = selectedByCategory.get(category.id) ?? 0}
      {@const open = openCategories.includes(category.id)}
      {@const shown = expanded.includes(category.id) ? songs : songs.slice(0, ROW_LIMIT)}
      {#if songs.length > 0}
        <Accordion.Item value={category.id}>
          <Accordion.Trigger class="hover:no-underline">
            <span class="flex min-w-0 flex-1 items-center justify-between gap-3 pr-3 text-left">
              <span class="truncate font-medium">{category.title}</span>
              <span class="text-xs font-normal text-muted-foreground">{selectedCount}/{category.song_count}</span>
            </span>
          </Accordion.Trigger>
          <Accordion.Content class="grid gap-2 pb-3">
            <!-- bits-ui keeps closed content mounted, and this list runs to
                 thousands of rows, so only build them once it is opened. -->
            {#if open}
              <div class="flex items-center justify-between rounded-md bg-muted/60 px-2.5 py-1 text-xs text-muted-foreground">
                <span>{songs.length} visible songs</span>
                <div class="flex gap-1">
                  <Button variant="ghost" size="sm" class="h-7" onclick={() => setSongs(songs, true)}>Select visible</Button>
                  <Button variant="ghost" size="sm" class="h-7" onclick={() => setSongs(songs, false)}>Clear visible</Button>
                </div>
              </div>
              <div class="grid gap-0.5 sm:grid-cols-2 2xl:grid-cols-3">
                {#each shown as song (song.id)}
                  {@const packageState = cabinet.package_states[song.id]}
                  {@const blocked = packageState?.state === "blocked"}
                  <Label
                    class={`flex cursor-pointer items-center gap-2.5 rounded-md border border-l-2 px-2 py-1 font-normal transition-colors hover:border-border hover:bg-accent/50 ${blocked ? "border-destructive/35 bg-destructive/5" : (song.source ?? "tja") === "osu" ? "border-transparent border-l-pink-500/60" : "border-transparent border-l-amber-500/60"}`}
                    title={blocked
                      ? `Blocked on this cabinet: ${packageState.error_code || "download or verification failed"}`
                      : `${song.id} · ${(song.source ?? "tja") === "osu" ? "osu!" : "TJA"}`}
                  >
                    <Checkbox checked={draftSet.has(song.id)} onCheckedChange={(value) => setSong(song.id, value === true)} />
                    <span class="min-w-0 truncate text-sm">{song.display_title || song.title}</span>
                    {#if blocked}
                      <Badge variant="destructive" class="ml-auto h-5 shrink-0 gap-1 px-1.5 text-[10px]">
                        <CircleAlertIcon class="size-3" /> Blocked
                      </Badge>
                    {/if}
                  </Label>
                {/each}
              </div>
              {#if shown.length < songs.length}
                <Button variant="outline" size="sm" class="h-7 justify-self-center" onclick={() => expanded = [...expanded, category.id]}>
                  Show the remaining {songs.length - shown.length} songs
                </Button>
              {/if}
            {/if}
          </Accordion.Content>
        </Accordion.Item>
      {/if}
    {/each}
  </Accordion.Root>

  {#if visibleSongs.length === 0}
    <div class="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">No songs match this view.</div>
  {/if}
</div>
