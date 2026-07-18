<script lang="ts">
  import AlertTriangleIcon from "@lucide/svelte/icons/triangle-alert";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import FolderUpIcon from "@lucide/svelte/icons/folder-up";
  import LoaderCircleIcon from "@lucide/svelte/icons/loader-circle";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import SearchIcon from "@lucide/svelte/icons/search";
  import Trash2Icon from "@lucide/svelte/icons/trash-2";
  import UploadIcon from "@lucide/svelte/icons/upload";
  import { onMount } from "svelte";
  import { deleteLibrarySong, deleteLibrarySongs, getManagedLibrary, retryLibrarySong, uploadOsz, uploadTja } from "$lib/api.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import { Checkbox } from "$lib/components/ui/checkbox/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import * as Tabs from "$lib/components/ui/tabs/index.js";
  import type { ManagedLibrary, ManagedSong } from "$lib/types.js";

  let { token, onChanged }: { token: string; onChanged: () => void | Promise<void> } = $props();

  const pageSize = 100;
  let data = $state<ManagedLibrary | null>(null);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let query = $state("");
  let health = $state("all");
  let categoryFilter = $state("all");
  let page = $state(1);
  let oszFile = $state<File | null>(null);
  let oszCategory = $state("");
  let tjaFiles = $state<File[]>([]);
  let tjaCategory = $state("");
  let checked = $state<string[]>([]);
  let deleteTargets = $state<ManagedSong[]>([]);
  let deleteOpen = $state(false);

  const categoryTitle = $derived.by(() => {
    const titles = new Map((data?.categories ?? []).map((category) => [category.id, category.title]));
    return (id: string) => titles.get(id) ?? id;
  });
  const songCategories = $derived.by(() => {
    const ids = [...new Set((data?.songs ?? []).map((song) => song.category))];
    return ids
      .map((id) => ({ id, title: categoryTitle(id) }))
      .sort((a, b) => a.title.localeCompare(b.title));
  });
  const normalizedQuery = $derived(query.trim().toLocaleLowerCase());
  const filtered = $derived.by(() => (data?.songs ?? []).filter((song) => {
    if (health !== "all" && song.conversion_status !== health) return false;
    if (categoryFilter !== "all" && song.category !== categoryFilter) return false;
    if (!normalizedQuery) return true;
    return [song.title, song.display_title, song.subtitle, song.id, song.category]
      .some((value) => value?.toLocaleLowerCase().includes(normalizedQuery));
  }));
  const pageCount = $derived(Math.max(1, Math.ceil(filtered.length / pageSize)));
  const visible = $derived(filtered.slice((page - 1) * pageSize, page * pageSize));
  const failedCount = $derived(data?.songs.filter((song) => song.conversion_status === "failed").length ?? 0);
  const readyCount = $derived(data?.songs.filter((song) => song.conversion_status === "ready").length ?? 0);
  const activeCount = $derived(data?.songs.filter((song) => ["queued", "processing"].includes(song.conversion_status)).length ?? 0);
  const checkedSet = $derived(new Set(checked));
  const allFilteredChecked = $derived(filtered.length > 0 && filtered.every((song) => checkedSet.has(song.id)));

  $effect(() => {
    query;
    health;
    categoryFilter;
    page = 1;
  });

  function toggleAllFiltered(value: boolean) {
    const ids = new Set(filtered.map((song) => song.id));
    checked = value
      ? [...new Set([...checked, ...ids])]
      : checked.filter((id) => !ids.has(id));
  }

  function folderPicker(node: HTMLInputElement) {
    node.setAttribute("webkitdirectory", "");
    node.setAttribute("directory", "");
  }

  async function load() {
    loading = true;
    error = "";
    try {
      data = await getManagedLibrary(token);
      if (!oszCategory) oszCategory = data.categories[0]?.id ?? "";
      if (!tjaCategory) tjaCategory = data.categories[0]?.id ?? "";
      const known = new Set(data.songs.map((song) => song.id));
      checked = checked.filter((id) => known.has(id));
      if (page > pageCount) page = pageCount;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not load the song library.";
    } finally {
      loading = false;
    }
  }

  async function afterMutation(message: string) {
    notice = message;
    await Promise.all([load(), onChanged()]);
  }

  async function submitOsz() {
    if (!oszFile) return;
    busy = true;
    error = "";
    try {
      await uploadOsz(token, oszFile, oszCategory);
      oszFile = null;
      await afterMutation("OSZ added to the library.");
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not upload the OSZ.";
    } finally { busy = false; }
  }

  async function submitTja() {
    if (!tjaFiles.length) return;
    busy = true;
    error = "";
    try {
      await uploadTja(token, tjaFiles, tjaCategory);
      tjaFiles = [];
      await afterMutation("TJA package added to the library.");
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not upload the TJA package.";
    } finally { busy = false; }
  }

  async function retry(song: ManagedSong) {
    busy = true;
    error = "";
    try {
      await retryLibrarySong(token, song.id);
      await afterMutation(`Retry queued for ${song.display_title || song.title}.`);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not retry the conversion.";
    } finally { busy = false; }
  }

  function requestDelete(songs: ManagedSong[]) {
    deleteTargets = songs;
    deleteOpen = true;
  }

  async function removeSongs() {
    if (!deleteTargets.length) return;
    busy = true;
    error = "";
    const targets = deleteTargets;
    try {
      if (targets.length === 1) {
        await deleteLibrarySong(token, targets[0].id);
      } else {
        await deleteLibrarySongs(token, targets.map((song) => song.id));
      }
      deleteOpen = false;
      deleteTargets = [];
      const removed = new Set(targets.map((song) => song.id));
      checked = checked.filter((id) => !removed.has(id));
      await afterMutation(targets.length === 1
        ? `${targets[0].display_title || targets[0].title} was removed.`
        : `${targets.length} songs were removed.`);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not delete the songs.";
    } finally { busy = false; }
  }

  function statusLabel(status: ManagedSong["conversion_status"]) {
    return status === "unconverted" ? "Not converted" : status[0].toUpperCase() + status.slice(1);
  }

  onMount(load);
</script>

<div class="grid gap-3">
  <details class="operator-panel group rounded-xl border">
    <summary class="flex cursor-pointer select-none items-center justify-between gap-3 px-4 py-3">
      <span class="flex items-center gap-2 text-sm font-semibold"><UploadIcon class="size-4 text-primary" /> Add songs</span>
      <span class="flex items-center gap-3 text-xs text-muted-foreground"><span class="hidden sm:inline">Uploads are validated before they become visible to cabinets.</span><ChevronDownIcon class="size-4 transition-transform group-open:rotate-180" /></span>
    </summary>
    <div class="border-t px-4 py-3">
      <Tabs.Root value="osz">
        <Tabs.List><Tabs.Trigger value="osz">osu! package (.osz)</Tabs.Trigger><Tabs.Trigger value="tja">TJA package</Tabs.Trigger></Tabs.List>
        <Tabs.Content value="osz" class="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-end">
          <div class="grid gap-1.5"><Label for="osz-file">OSZ file</Label><Input id="osz-file" type="file" accept=".osz" onchange={(event) => oszFile = event.currentTarget.files?.[0] ?? null} /></div>
          <div class="grid gap-1.5"><Label for="osz-category">Category</Label><select id="osz-category" bind:value={oszCategory} class="h-9 rounded-md border bg-background px-3 text-sm">{#each data?.categories ?? [] as category}<option value={category.id}>{category.title}</option>{/each}</select></div>
          <Button disabled={!oszFile || busy} onclick={submitOsz}>{#if busy}<LoaderCircleIcon class="animate-spin" />{:else}<UploadIcon />{/if} Add OSZ</Button>
        </Tabs.Content>
        <Tabs.Content value="tja" class="mt-3 grid gap-3">
          <div class="grid gap-3 md:grid-cols-2">
            <div class="grid gap-1.5"><Label for="tja-files">TJA and audio files</Label><Input id="tja-files" type="file" multiple accept=".tja,.ogg,.wav,.mp3,.flac,.png,.jpg,.jpeg" onchange={(event) => tjaFiles = Array.from(event.currentTarget.files ?? [])} /></div>
            <div class="grid gap-1.5"><Label for="tja-folder">Whole song folder</Label><input id="tja-folder" class="file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm" type="file" multiple use:folderPicker onchange={(event) => tjaFiles = Array.from(event.currentTarget.files ?? [])} /></div>
          </div>
          <div class="grid gap-3 md:grid-cols-[220px_auto] md:items-end md:justify-start">
            <div class="grid gap-1.5"><Label for="tja-category">Category</Label><select id="tja-category" bind:value={tjaCategory} class="h-9 rounded-md border bg-background px-3 text-sm">{#each data?.categories ?? [] as category}<option value={category.id}>{category.title}</option>{/each}</select></div>
            <Button disabled={!tjaFiles.length || busy} onclick={submitTja}>{#if busy}<LoaderCircleIcon class="animate-spin" />{:else}<FolderUpIcon />{/if} Add {tjaFiles.length || ""} files</Button>
          </div>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  </details>

  <Card.Root class="operator-panel gap-0 py-0" size="sm">
    <Card.Header class="gap-3 border-b px-4 py-3">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Card.Title class="text-base">Song library</Card.Title>
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span><span class="font-semibold text-foreground">{data?.songs.length ?? 0}</span> songs</span>
          <span><span class="font-semibold text-primary">{readyCount}</span> ready</span>
          <span><span class="font-semibold {failedCount ? 'text-destructive' : 'text-foreground'}">{failedCount}</span> broken</span>
          <span><span class="font-semibold {activeCount ? 'text-amber-600 dark:text-amber-400' : 'text-foreground'}">{activeCount}</span> converting</span>
        </div>
      </div>
      <div class="flex flex-col gap-2 sm:flex-row">
        <div class="relative min-w-0 flex-1 sm:max-w-72"><SearchIcon class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input bind:value={query} class="h-8 pl-9" placeholder="Search songs…" /></div>
        <select bind:value={categoryFilter} aria-label="Category" class="h-8 max-w-56 rounded-md border bg-background px-2 text-sm"><option value="all">All categories</option>{#each songCategories as category (category.id)}<option value={category.id}>{category.title}</option>{/each}</select>
        <select bind:value={health} aria-label="Conversion health" class="h-8 rounded-md border bg-background px-2 text-sm"><option value="all">All health</option><option value="failed">Broken</option><option value="ready">Ready</option><option value="unconverted">Not converted</option><option value="processing">Processing</option><option value="queued">Queued</option></select>
        <Button variant="outline" size="icon-sm" aria-label="Refresh library" disabled={loading} onclick={load}><RefreshCwIcon class={loading ? "animate-spin" : ""} /></Button>
      </div>
    </Card.Header>
    <Card.Content class="grid gap-2 p-3">
      {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}
      {#if notice}<p class="rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-sm text-primary">{notice}</p>{/if}
      {#if loading && !data}<div class="grid min-h-56 place-items-center"><LoaderCircleIcon class="size-6 animate-spin text-muted-foreground" /></div>
      {:else if !visible.length}<div class="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">No songs match this view.</div>
      {:else}
        <div class="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/60 px-2.5 py-1.5 text-xs text-muted-foreground">
          <label class="flex cursor-pointer items-center gap-2">
            <Checkbox checked={allFilteredChecked} onCheckedChange={(value) => toggleAllFiltered(value === true)} aria-label="Select all filtered songs" />
            <span>{checked.length ? `${checked.length} selected` : `Select all ${filtered.length} filtered`}</span>
          </label>
          {#if checked.length}
            <div class="flex gap-2">
              <Button variant="ghost" size="sm" class="h-7" disabled={busy} onclick={() => checked = []}>Clear</Button>
              <Button variant="destructive" size="sm" class="h-7" disabled={busy} onclick={() => requestDelete((data?.songs ?? []).filter((song) => checkedSet.has(song.id)))}><Trash2Icon /> Delete {checked.length}</Button>
            </div>
          {/if}
        </div>
        <div class="overflow-hidden rounded-lg border bg-background/50">
          {#each visible as song (song.id)}
            <div class="flex items-center gap-2.5 border-b px-2.5 py-1.5 last:border-b-0 hover:bg-accent/40">
              <Checkbox checked={checkedSet.has(song.id)} aria-label={`Select ${song.title}`} onCheckedChange={(value) => {
                checked = value === true ? [...checked, song.id] : checked.filter((id) => id !== song.id);
              }} />
              <div class="min-w-0 flex-1">
                <div class="flex items-baseline gap-2">
                  <p class="truncate text-sm font-medium">{song.display_title || song.title}</p>
                  <p class="hidden shrink-0 font-mono text-[10px] text-muted-foreground md:block">{song.id}</p>
                  {#if song.conversion_status === "failed"}<AlertTriangleIcon class="size-3.5 shrink-0 self-center text-destructive" />{/if}
                </div>
                {#if song.conversion_error}<p class="truncate text-xs text-destructive" title={song.conversion_error}>{song.conversion_error}</p>{/if}
              </div>
              <div class="hidden w-36 truncate text-xs text-muted-foreground sm:block">{categoryTitle(song.category)}</div>
              <Badge variant={song.conversion_status === "failed" ? "destructive" : song.conversion_status === "ready" ? "default" : "secondary"} class="h-5 px-1.5 text-[10px]">{statusLabel(song.conversion_status)}</Badge>
              <Badge variant="outline" class="hidden h-5 px-1.5 text-[10px] sm:inline-flex">{song.source.toUpperCase()}</Badge>
              <div class="flex w-20 justify-end gap-1">
                {#if song.conversion_status === "failed"}<Button variant="ghost" size="icon-sm" class="size-7" aria-label={`Retry ${song.title}`} disabled={busy} onclick={() => retry(song)}><RefreshCwIcon /></Button>{/if}
                <Button variant="ghost" size="icon-sm" class="size-7 text-destructive hover:text-destructive" aria-label={`Delete ${song.title}`} disabled={busy} onclick={() => requestDelete([song])}><Trash2Icon /></Button>
              </div>
            </div>
          {/each}
        </div>
        <div class="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>{filtered.length} songs · page {page} of {pageCount}</span><div class="flex gap-1"><Button variant="outline" size="icon-sm" class="size-7" disabled={page <= 1} onclick={() => page--}><ChevronLeftIcon /></Button><Button variant="outline" size="icon-sm" class="size-7" disabled={page >= pageCount} onclick={() => page++}><ChevronRightIcon /></Button></div></div>
      {/if}
    </Card.Content>
  </Card.Root>
</div>

<AlertDialog.Root bind:open={deleteOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>{deleteTargets.length === 1 ? "Remove this song?" : `Remove ${deleteTargets.length} songs?`}</AlertDialog.Title>
      <AlertDialog.Description>
        {deleteTargets.length === 1 ? `“${deleteTargets[0]?.display_title || deleteTargets[0]?.title}” will be deleted.` : `${deleteTargets.length} songs will be deleted.`}
        This removes the source chart/package and its generated conversion, and drops it from every cabinet selection.
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer><AlertDialog.Cancel disabled={busy}>Cancel</AlertDialog.Cancel><AlertDialog.Action variant="destructive" disabled={busy} onclick={removeSongs}>{busy ? "Removing…" : deleteTargets.length === 1 ? "Remove song" : `Remove ${deleteTargets.length} songs`}</AlertDialog.Action></AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
