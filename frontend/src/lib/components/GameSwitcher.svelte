<script lang="ts">
  import Gamepad2Icon from "@lucide/svelte/icons/gamepad-2";
  import PowerIcon from "@lucide/svelte/icons/power";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import SaveIcon from "@lucide/svelte/icons/save";
  import { launchInstalledGame, refreshInstalledGames, saveGameAutoboot } from "$lib/api.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import type { Cabinet, InstalledGame } from "$lib/types.js";

  let { token, cabinet }: { token: string; cabinet: Cabinet } = $props();

  let autoboot = $state("");
  let delay = $state(15);
  let dirty = $state(false);
  let saving = $state(false);
  let refreshing = $state(false);
  let launching = $state(false);
  let launchOpen = $state(false);
  let launchGame = $state<InstalledGame | null>(null);
  let error = $state("");
  let notice = $state("");
  const games = $derived(cabinet.installed_games ?? []);

  $effect(() => {
    if (!dirty && !saving) {
      autoboot = cabinet.autoboot_dir || "";
      delay = cabinet.autoboot_delay ?? 15;
    }
  });

  function iconUrl(game: InstalledGame) {
    return `/api/ui/cabinets/${encodeURIComponent(cabinet.cabinet_id)}/games/${encodeURIComponent(game.directory)}/icon`;
  }

  async function refresh() {
    refreshing = true;
    error = "";
    notice = "";
    try {
      await refreshInstalledGames(token, cabinet.cabinet_id);
      notice = "Inventory requested. The list and icons will update when the console finishes scanning.";
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      refreshing = false;
    }
  }

  async function saveAutoboot() {
    saving = true;
    error = "";
    notice = "";
    try {
      const normalizedDelay = Math.max(0, Math.min(600, Number(delay) || 0));
      await saveGameAutoboot(token, cabinet.cabinet_id, autoboot, normalizedDelay);
      delay = normalizedDelay;
      dirty = false;
      notice = autoboot
        ? "Autoboot sent to the console. It will apply on the next reboot."
        : "Autoboot disabled on the console.";
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      saving = false;
    }
  }

  function requestLaunch(game: InstalledGame) {
    launchGame = game;
    error = "";
    launchOpen = true;
  }

  async function confirmLaunch() {
    if (!launchGame) return;
    launching = true;
    error = "";
    notice = "";
    try {
      await launchInstalledGame(token, cabinet.cabinet_id, launchGame.directory);
      notice = `Switching to ${launchGame.title}. The current game will close before the new one starts.`;
      launchOpen = false;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      launching = false;
    }
  }
</script>

<div class="grid gap-4">
  <div class="flex flex-wrap items-start justify-between gap-3 rounded-lg border bg-muted/30 p-3">
    <div>
      <div class="flex items-center gap-2 text-sm font-semibold">
        <Gamepad2Icon class="size-4" /> Installed games
      </div>
      <p class="mt-1 max-w-3xl text-xs text-muted-foreground">
        Reported from direct children of <span class="font-mono">/dev_hdd0/game</span>. Disc mounts, ISOs,
        JB folders and USB games are intentionally excluded.
      </p>
      {#if cabinet.games_updated_at}
        <p class="mt-1 text-[11px] text-muted-foreground">
          Last scanned {new Date(cabinet.games_updated_at * 1000).toLocaleString()}
        </p>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      <Badge variant={cabinet.agent_online ? "outline" : "secondary"}>
        VSH agent {cabinet.agent_online ? "online" : "offline"}
      </Badge>
      <Button variant="outline" size="sm" disabled={!cabinet.agent_online || refreshing} onclick={refresh}>
        <RefreshCwIcon class={refreshing ? "animate-spin" : ""} />
        {refreshing ? "Scanning…" : "Refresh"}
      </Button>
    </div>
  </div>

  <section class="grid gap-3 rounded-lg border p-3">
    <div>
      <h3 class="text-sm font-semibold">Startup game</h3>
      <p class="mt-0.5 text-xs text-muted-foreground">
        The VSH agent waits for XMB and launches this installed directory once per boot.
      </p>
    </div>
    <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_auto] sm:items-end">
      <label class="grid gap-1 text-xs font-medium">
        Game
        <select
          class="h-9 min-w-0 rounded-md border bg-background px-3 text-sm"
          value={autoboot}
          onchange={(event) => { autoboot = event.currentTarget.value; dirty = true; }}
        >
          <option value="">Disabled — stop at XMB</option>
          {#each games as game (game.directory)}
            <option value={game.directory}>{game.title} — {game.directory}</option>
          {/each}
        </select>
      </label>
      <label class="grid gap-1 text-xs font-medium">
        Delay (seconds)
        <input
          class="h-9 rounded-md border bg-background px-3 text-sm"
          type="number"
          min="0"
          max="600"
          value={delay}
          oninput={(event) => { delay = Number(event.currentTarget.value); dirty = true; }}
        />
      </label>
      <Button disabled={!cabinet.agent_online || saving || !dirty} onclick={saveAutoboot}>
        <SaveIcon /> {saving ? "Saving…" : "Save autoboot"}
      </Button>
    </div>
  </section>

  {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}
  {#if notice}<p class="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">{notice}</p>{/if}

  {#if games.length}
    <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {#each games as game (game.directory)}
        <article class="flex min-w-0 gap-3 rounded-lg border bg-card p-3">
          <div class="grid size-20 shrink-0 place-items-center overflow-hidden rounded-md border bg-muted">
            {#if game.has_icon}
              <img class="size-full object-cover" src={iconUrl(game)} alt="" />
            {:else}
              <Gamepad2Icon class="size-8 text-muted-foreground" />
            {/if}
          </div>
          <div class="flex min-w-0 flex-1 flex-col">
            <div class="flex items-start justify-between gap-2">
              <h3 class="line-clamp-2 text-sm font-semibold">{game.title}</h3>
              {#if cabinet.autoboot_dir === game.directory}
                <Badge variant="outline" class="shrink-0 text-[10px]">Autoboot</Badge>
              {/if}
            </div>
            <p class="mt-1 truncate font-mono text-[11px] text-muted-foreground">{game.directory}</p>
            <p class="truncate text-[11px] text-muted-foreground">
              {game.title_id}{game.version ? ` · v${game.version}` : ""}
            </p>
            <div class="mt-auto pt-2">
              <Button
                size="sm"
                class="w-full"
                disabled={!cabinet.agent_online}
                onclick={() => requestLaunch(game)}
              >
                <PowerIcon /> Switch to this game
              </Button>
            </div>
          </div>
        </article>
      {/each}
    </div>
  {:else}
    <div class="grid min-h-40 place-items-center rounded-lg border border-dashed p-6 text-center">
      <div>
        <Gamepad2Icon class="mx-auto size-8 text-muted-foreground" />
        <p class="mt-2 text-sm font-medium">No installed-game inventory yet</p>
        <p class="mt-1 text-xs text-muted-foreground">
          Load the updated VSH agent, then use Refresh while the console is reachable.
        </p>
      </div>
    </div>
  {/if}
</div>

<AlertDialog.Root bind:open={launchOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>Switch to {launchGame?.title || "this game"}?</AlertDialog.Title>
      <AlertDialog.Description>
        This immediately ends any running game, credit, and play in progress. The VSH agent waits for XMB,
        then launches <span class="font-mono">{launchGame?.directory}</span> through Sony's stock installed-game pipeline.
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel disabled={launching}>Cancel</AlertDialog.Cancel>
      <AlertDialog.Action variant="destructive" disabled={launching} onclick={confirmLaunch}>
        {launching ? "Switching…" : "Exit and launch"}
      </AlertDialog.Action>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
