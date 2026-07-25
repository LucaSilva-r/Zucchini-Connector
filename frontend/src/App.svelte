<script lang="ts">
  import MoonIcon from "@lucide/svelte/icons/moon";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import LibraryBigIcon from "@lucide/svelte/icons/library-big";
  import LockKeyholeIcon from "@lucide/svelte/icons/lock-keyhole";
  import ServerIcon from "@lucide/svelte/icons/server";
  import SunIcon from "@lucide/svelte/icons/sun";
  import UnplugIcon from "@lucide/svelte/icons/unplug";
  import { onMount } from "svelte";
  import { ApiError, getCabinets, getLibrary } from "$lib/api.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import { Skeleton } from "$lib/components/ui/skeleton/index.js";
  import CabinetDashboard from "$lib/components/CabinetDashboard.svelte";
  import CabinetList from "$lib/components/CabinetList.svelte";
  import LibraryManager from "$lib/components/LibraryManager.svelte";
  import ManagementDialog from "$lib/components/ManagementDialog.svelte";
  import { lock, management, refreshManagement } from "$lib/management.svelte.js";
  import type { Cabinet, Library } from "$lib/types.js";

  const token = "";
  let cabinets = $state<Cabinet[]>([]);
  let library = $state<Library | null>(null);
  let selectedId = $state<string | null>(null);
  let loading = $state(true);
  let refreshing = $state(false);
  let authorized = $state(false);
  let error = $state("");
  let view = $state<"cabinets" | "library">("cabinets");
  let dark = $state(localStorage.getItem("connector_theme") === "dark" ||
    (!localStorage.getItem("connector_theme") && matchMedia("(prefers-color-scheme: dark)").matches));

  const selectedCabinet = $derived(cabinets.find((cabinet) => cabinet.cabinet_id === selectedId) ?? null);

  function applyTheme() {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("connector_theme", dark ? "dark" : "light");
  }

  function replaceCabinet(updated: Cabinet) {
    cabinets = cabinets.map((cabinet) => cabinet.cabinet_id === updated.cabinet_id ? updated : cabinet);
  }

  async function connect() {
    loading = true;
    error = "";
    try {
      const [cabinetResponse, loadedLibrary] = await Promise.all([
        getCabinets(token),
        getLibrary(token),
      ]);
      cabinets = cabinetResponse.cabinets;
      library = loadedLibrary;
      authorized = true;
      if (!selectedId || !cabinets.some((cabinet) => cabinet.cabinet_id === selectedId)) {
        selectedId = cabinets[0]?.cabinet_id ?? null;
      }
    } catch (reason) {
      authorized = false;
      error = reason instanceof Error ? reason.message : "Could not reach the connector.";
    } finally {
      loading = false;
    }
  }

  async function refreshCabinets(silent = false) {
    if (!authorized || refreshing || document.hidden) return;
    if (!silent) refreshing = true;
    try {
      cabinets = (await getCabinets(token)).cabinets;
      if (selectedId && !cabinets.some((cabinet) => cabinet.cabinet_id === selectedId)) {
        selectedId = cabinets[0]?.cabinet_id ?? null;
      }
      error = "";
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) authorized = false;
      error = reason instanceof Error ? reason.message : "Could not refresh cabinets.";
    } finally {
      refreshing = false;
    }
  }

  async function refreshAfterLibraryChange() {
    const [cabinetResponse, loadedLibrary] = await Promise.all([getCabinets(token), getLibrary(token)]);
    cabinets = cabinetResponse.cabinets;
    library = loadedLibrary;
  }

  function removeCabinet(id: string) {
    cabinets = cabinets.filter((cabinet) => cabinet.cabinet_id !== id);
    selectedId = cabinets[0]?.cabinet_id ?? null;
  }

  onMount(() => {
    applyTheme();
    connect();
    refreshManagement();
    const timer = window.setInterval(() => refreshCabinets(true), 10_000);
    const visibleRefresh = () => { if (!document.hidden) refreshCabinets(true); };
    document.addEventListener("visibilitychange", visibleRefresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibleRefresh);
    };
  });
</script>

<svelte:head>
  <title>Zucchini Connector</title>
  <meta name="description" content="TaikoZucchini arcade cabinet operations dashboard" />
</svelte:head>

<header class="sticky top-0 z-40 border-b bg-background/85 backdrop-blur-xl">
  <div class="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
    <div class="min-w-0">
      <div class="flex items-center gap-2">
        <span class="grid size-8 place-items-center rounded-lg bg-primary font-black text-primary-foreground">Z</span>
        <div><h1 class="truncate text-sm font-semibold tracking-tight sm:text-base">Zucchini Connector</h1><p class="hidden text-xs text-muted-foreground sm:block">Arcade operations console</p></div>
      </div>
    </div>
    <div class="flex items-center gap-2">
      {#if authorized}
        <div class="hidden items-center rounded-lg bg-muted p-1 sm:flex">
          <Button variant={view === "cabinets" ? "secondary" : "ghost"} size="sm" onclick={() => view = "cabinets"}><ServerIcon /> Cabinets</Button>
          <Button variant={view === "library" ? "secondary" : "ghost"} size="sm" onclick={() => view = "library"}><LibraryBigIcon /> Song library</Button>
        </div>
        <Button variant="ghost" size="icon" aria-label="Refresh cabinets" onclick={() => refreshCabinets(false)}><RefreshCwIcon class={refreshing ? "animate-spin" : ""} /></Button>
      {/if}
      {#if management.unlocked}
        <Button variant="ghost" size="icon" aria-label="Lock management controls" title="Lock management controls" onclick={lock}><LockKeyholeIcon /></Button>
      {/if}
      <Button variant="ghost" size="icon" aria-label="Toggle color theme" onclick={() => { dark = !dark; applyTheme(); }}>
        {#if dark}<SunIcon />{:else}<MoonIcon />{/if}
      </Button>
    </div>
  </div>
</header>

<main class="mx-auto w-full max-w-[1600px] p-3 sm:p-4">
  {#if !authorized}
    <div class="mx-auto grid min-h-[70vh] max-w-md place-items-center">
      <Card.Root class="operator-panel w-full">
        <Card.Header>
          <Card.Title>Could not load the connector</Card.Title>
          <Card.Description>The public song and cabinet dashboard is unavailable.</Card.Description>
        </Card.Header>
        <Card.Content>
          <div class="grid gap-4">
            {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}
            <Button onclick={connect} disabled={loading}>{loading ? "Retrying…" : "Retry"}</Button>
          </div>
        </Card.Content>
      </Card.Root>
    </div>
  {:else if loading || !library}
    <div class="grid gap-4 lg:grid-cols-[270px_minmax(0,1fr)]">
      <Skeleton class="h-96 rounded-xl" /><Skeleton class="h-[640px] rounded-xl" />
    </div>
  {:else}
    {#if error}<div class="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"><UnplugIcon class="size-4" /> {error}</div>{/if}
    <div class="mb-4 grid grid-cols-2 rounded-lg bg-muted p-1 sm:hidden"><Button variant={view === "cabinets" ? "secondary" : "ghost"} size="sm" onclick={() => view = "cabinets"}><ServerIcon /> Cabinets</Button><Button variant={view === "library" ? "secondary" : "ghost"} size="sm" onclick={() => view = "library"}><LibraryBigIcon /> Library</Button></div>
    {#if view === "library"}
      <LibraryManager {token} onChanged={refreshAfterLibraryChange} />
    {:else}
      <div class="grid items-start gap-4 lg:grid-cols-[270px_minmax(0,1fr)]">
        <CabinetList {cabinets} {selectedId} onSelect={(id) => selectedId = id} />
        {#if selectedCabinet}
          <CabinetDashboard {token} cabinet={selectedCabinet} {library} onUpdated={replaceCabinet} onDeleted={removeCabinet} />
        {:else}
          <Card.Root class="operator-panel"><Card.Content class="grid min-h-80 place-items-center text-center text-sm text-muted-foreground">Select a cabinet after it checks in.</Card.Content></Card.Root>
        {/if}
      </div>
    {/if}
  {/if}
</main>

<ManagementDialog />
