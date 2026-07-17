<script lang="ts">
  import KeyRoundIcon from "@lucide/svelte/icons/key-round";
  import MoonIcon from "@lucide/svelte/icons/moon";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import SunIcon from "@lucide/svelte/icons/sun";
  import UnplugIcon from "@lucide/svelte/icons/unplug";
  import { onMount } from "svelte";
  import { ApiError, getCabinets, getLibrary } from "$lib/api.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Skeleton } from "$lib/components/ui/skeleton/index.js";
  import CabinetDashboard from "$lib/components/CabinetDashboard.svelte";
  import CabinetList from "$lib/components/CabinetList.svelte";
  import type { Cabinet, Library } from "$lib/types.js";

  const storedToken = localStorage.getItem("connector_token") || "";
  let token = $state(storedToken);
  let tokenDraft = $state(storedToken);
  let cabinets = $state<Cabinet[]>([]);
  let library = $state<Library | null>(null);
  let selectedId = $state<string | null>(null);
  let loading = $state(true);
  let refreshing = $state(false);
  let authorized = $state(false);
  let error = $state("");
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
        getCabinets(tokenDraft.trim()),
        getLibrary(tokenDraft.trim()),
      ]);
      token = tokenDraft.trim();
      localStorage.setItem("connector_token", token);
      cabinets = cabinetResponse.cabinets;
      library = loadedLibrary;
      authorized = true;
      if (!selectedId || !cabinets.some((cabinet) => cabinet.cabinet_id === selectedId)) {
        selectedId = cabinets[0]?.cabinet_id ?? null;
      }
    } catch (reason) {
      authorized = false;
      error = reason instanceof ApiError && reason.status === 401
        ? "The API token was rejected."
        : reason instanceof Error ? reason.message : "Could not reach the connector.";
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

  function removeCabinet(id: string) {
    cabinets = cabinets.filter((cabinet) => cabinet.cabinet_id !== id);
    selectedId = cabinets[0]?.cabinet_id ?? null;
  }

  function changeToken() {
    authorized = false;
    tokenDraft = token;
    error = "";
  }

  onMount(() => {
    applyTheme();
    connect();
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
        <Button variant="ghost" size="icon" aria-label="Refresh cabinets" onclick={() => refreshCabinets(false)}><RefreshCwIcon class={refreshing ? "animate-spin" : ""} /></Button>
        <Button variant="ghost" size="sm" onclick={changeToken}><KeyRoundIcon /> Token</Button>
      {/if}
      <Button variant="ghost" size="icon" aria-label="Toggle color theme" onclick={() => { dark = !dark; applyTheme(); }}>
        {#if dark}<SunIcon />{:else}<MoonIcon />{/if}
      </Button>
    </div>
  </div>
</header>

<main class="mx-auto w-full max-w-[1600px] p-4 sm:p-6">
  {#if !authorized}
    <div class="mx-auto grid min-h-[70vh] max-w-md place-items-center">
      <Card.Root class="operator-panel w-full">
        <Card.Header>
          <Card.Title>Connect to your cabinet fleet</Card.Title>
          <Card.Description>Enter the bearer token configured on this connector.</Card.Description>
        </Card.Header>
        <Card.Content>
          <form class="grid gap-4" onsubmit={(event) => { event.preventDefault(); connect(); }}>
            <div class="grid gap-2"><Label for="api-token">API token</Label><Input id="api-token" type="password" bind:value={tokenDraft} autocomplete="current-password" autofocus /></div>
            {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}
            <Button type="submit" disabled={loading}>{loading ? "Connecting…" : "Connect"}</Button>
          </form>
        </Card.Content>
      </Card.Root>
    </div>
  {:else if loading || !library}
    <div class="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
      <Skeleton class="h-96 rounded-xl" /><Skeleton class="h-[640px] rounded-xl" />
    </div>
  {:else}
    {#if error}<div class="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"><UnplugIcon class="size-4" /> {error}</div>{/if}
    <div class="grid items-start gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
      <CabinetList {cabinets} {selectedId} onSelect={(id) => selectedId = id} />
      {#if selectedCabinet}
        <CabinetDashboard {token} cabinet={selectedCabinet} {library} onUpdated={replaceCabinet} onDeleted={removeCabinet} />
      {:else}
        <Card.Root class="operator-panel"><Card.Content class="grid min-h-80 place-items-center text-center text-sm text-muted-foreground">Select a cabinet after it checks in.</Card.Content></Card.Root>
      {/if}
    </div>
  {/if}
</main>
