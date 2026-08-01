<script lang="ts">
  import ThermometerIcon from "@lucide/svelte/icons/thermometer";
  import Gamepad2Icon from "@lucide/svelte/icons/gamepad-2";
  import CameraIcon from "@lucide/svelte/icons/camera";
  import {
    captureScreenshot,
    pressPadButton,
    runWebmanAction,
    screenshotUrl,
    type DangerAction,
    type PadButton,
  } from "$lib/api.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import type { Cabinet } from "$lib/types.js";

  let { token, cabinet }: { token: string; cabinet: Cabinet } = $props();

  // Relayed from webMAN's own info page every ~2 min, with the poll's
  // temperatures laid over it. Every field is optional: an edition that
  // prints fewer figures simply shows fewer rows.
  const health = $derived(cabinet.agent_health ?? { cpu_temp: 0, rsx_temp: 0, fan_percent: 0 });
  // 80 C is where webMAN's own dynamic fan control is already at full tilt.
  const hot = $derived(Math.max(health.cpu_temp, health.rsx_temp) >= 80);
  const rows = $derived(
    [
      ["CPU", health.cpu_temp ? `${health.cpu_temp} °C` : "", true],
      ["RSX", health.rsx_temp ? `${health.rsx_temp} °C` : "", true],
      ["Fan", health.fan_percent ? `${health.fan_percent}%${health.max_temp ? ` (max ${health.max_temp} °C)` : ""}` : "", false],
      ["Memory", health.mem_kb ? `${health.mem_kb.toLocaleString()} KB free` : "", false],
      ["HDD", health.hdd_free ?? "", false],
      ["Clocks", health.gpu_mhz ? `GPU ${health.gpu_mhz} MHz · VRAM ${health.vram_mhz ?? 0} MHz` : "", false],
      ["Firmware", health.firmware ?? "", false],
      ["Lifetime", health.runtime ?? "", false],
    ].filter(([, value]) => value) as [string, string, boolean][],
  );
  const age = $derived(health.updated_at ? Math.max(0, Math.round(Date.now() / 1000 - health.updated_at)) : 0);

  // webMAN runs on the console itself, so these keep working after the game
  // process is gone — which is the point of restart_game.
  const WEBMAN_LABELS: Record<DangerAction, { label: string; confirm: string }> = {
    restart_game: {
      label: "Restart game",
      confirm:
        "Closes the game, waits for XMB and presses X on the game icon to launch it again. Any credit or play in progress is lost. This is how a downloaded plugin update gets applied without a site visit.",
    },
    exit_game: {
      label: "Exit to XMB",
      confirm:
        "Closes the game and leaves the cabinet on XMB. It stops answering this connector until the game runs again.",
    },
    reboot: {
      label: "Reboot console",
      confirm:
        "Soft-reboots the PS3. The cabinet is offline until it boots and the game auto-starts.",
    },
  };
  let pending = $state<DangerAction | null>(null);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let open = $state(false);

  async function runWebman(action: DangerAction) {
    busy = true;
    error = "";
    try {
      await runWebmanAction(token, cabinet.cabinet_id, action);
      notice = `${WEBMAN_LABELS[action].label} sent to the console.`;
      pending = null;
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }

  // Virtual pad. webMAN inserts one 70 ms press per request, so this is a
  // click-per-press remote — good enough to drive the XMB, and the reason the
  // screenshot below sits next to it: without a picture you are pressing
  // buttons blind.
  let padBusy = $state(false);
  let padError = $state("");

  async function press(button: PadButton) {
    padBusy = true;
    padError = "";
    try {
      await pressPadButton(token, cabinet.cabinet_id, button);
    } catch (err) {
      padError = err instanceof Error ? err.message : String(err);
    } finally {
      padBusy = false;
    }
  }

  // Either half can capture: the plugin while the game runs, webMAN once it has
  // exited — and on XMB, which is where the pad is useful, it is the agent.
  const canCapture = $derived(cabinet.agent_online || cabinet.control_online);
  let shotBusy = $state(false);
  let shotError = $state("");
  let shotStamp = $state(0);
  const shotUrl = $derived(shotStamp ? screenshotUrl(cabinet.cabinet_id, shotStamp) : "");

  async function takeScreenshot() {
    shotBusy = true;
    shotError = "";
    try {
      shotStamp = await captureScreenshot(token, cabinet.cabinet_id);
    } catch (err) {
      shotError = err instanceof Error ? err.message : String(err);
    } finally {
      shotBusy = false;
    }
  }
</script>

<div class="grid gap-4">
  <div class="rounded-lg border p-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2 text-sm font-semibold"><ThermometerIcon class="size-4" /> Console health</div>
      <span class="text-xs text-muted-foreground">
        {#if !cabinet.agent_online}
          Agent offline — these are the last figures it sent.
        {:else if age}
          Read {age < 90 ? `${age}s` : `${Math.round(age / 60)}m`} ago, from webMAN on the console.
        {:else}
          Waiting for the console's first report.
        {/if}
      </span>
    </div>

    {#if rows.length}
      <dl class="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">
        {#each rows as [label, value, isTemp] (label)}
          <div class="flex justify-between gap-3 border-b border-dashed py-1">
            <dt class="text-muted-foreground">{label}</dt>
            <dd class={isTemp && hot ? "font-medium text-destructive" : "text-right font-medium"}>{value}</dd>
          </div>
        {/each}
      </dl>
    {:else}
      <p class="mt-3 text-sm text-muted-foreground">
        Nothing reported yet. The agent relays webMAN's console page every fourth poll, so this fills in within a couple of minutes of the
        cabinet coming online.
      </p>
    {/if}

    {#if health.text}
      <!-- webMAN prints more than this parser looks for, and editions differ.
           The stripped page is kept so nothing it reported is lost here. -->
      <details class="mt-3">
        <summary class="cursor-pointer text-xs text-muted-foreground">Everything webMAN reported</summary>
        <pre class="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-2 text-[11px]">{health.text}</pre>
      </details>
    {/if}
  </div>

{#snippet pad(button: PadButton, label: string, extra = "")}
  <Button
    variant="outline"
    size="sm"
    class={`h-10 ${extra}`}
    disabled={!cabinet.agent_online || padBusy}
    onclick={() => press(button)}
  >
    {label}
  </Button>
{/snippet}

  <div class="grid gap-3 rounded-lg border p-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2 text-sm font-semibold"><Gamepad2Icon class="size-4" /> Virtual controller</div>
      <Button variant="outline" size="sm" disabled={!canCapture || shotBusy} onclick={takeScreenshot}>
        <CameraIcon /> {shotBusy ? "Capturing…" : shotStamp ? "Refresh screen" : "Screenshot"}
      </Button>
    </div>
    <p class="text-xs text-muted-foreground">
      A pad webMAN fakes on the console, so it steers the XMB with no game running — which is when the cabinet's own drum controls nothing.
      Each button is a single short press; take a screenshot to see where the cursor actually is.
      {#if !cabinet.agent_online}
        <span class="text-destructive">Agent offline — these will fail until it polls again.</span>
      {/if}
    </p>

    {#if padError}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{padError}</p>{/if}

    <div class="flex flex-wrap items-start justify-center gap-6 select-none">
      <div class="grid gap-2">
        <div class="flex gap-2">{@render pad("l2", "L2")}{@render pad("l1", "L1")}</div>
        <div class="grid grid-cols-3 gap-1">
          <span></span>{@render pad("up", "↑")}<span></span>
          {@render pad("left", "←")}<span></span>{@render pad("right", "→")}
          <span></span>{@render pad("down", "↓")}<span></span>
        </div>
      </div>

      <div class="grid gap-2">
        <div class="flex justify-end gap-2">{@render pad("r1", "R1")}{@render pad("r2", "R2")}</div>
        <div class="grid grid-cols-3 gap-1">
          <span></span>{@render pad("triangle", "△")}<span></span>
          {@render pad("square", "▢")}<span></span>{@render pad("circle", "○")}
          <!-- X is enter on this console's button assign; webMAN's own
               restart_game chain relies on the same press. -->
          <span></span>{@render pad("cross", "✕", "border-primary/60 text-primary")}<span></span>
        </div>
      </div>
    </div>

    <div class="flex flex-wrap items-center justify-center gap-2">
      {@render pad("select", "Select")}
      {@render pad("start", "Start")}
      {@render pad("psbtn", "PS")}
      {@render pad("l3", "L3")}
      {@render pad("r3", "R3")}
      <!-- Unregisters the fake controller. Here because it is the way out if
           the extra pad port ever bothers the real drum. -->
      {@render pad("off", "Disconnect pad", "text-muted-foreground")}
    </div>

    {#if shotError}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{shotError}</p>{/if}
    {#if shotUrl}
      <img src={shotUrl} alt="Console screen" class="w-full max-w-2xl self-center rounded-md border" />
    {/if}
  </div>

  <!-- Kept behind a fold even on its own tab: these are the only controls here
       that a mis-click cannot be walked back from. -->
  <details bind:open class="rounded-lg border border-destructive/40 bg-destructive/5">
    <summary class="cursor-pointer select-none px-3 py-2 text-sm font-semibold">
      Console control (webMAN) — dangerous
    </summary>
    <div class="grid gap-3 border-t border-destructive/30 p-3">
      <p class="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
        <strong>These act on the PS3 itself, not on the game.</strong> They end any credit or play in progress immediately, and a cabinet
        that fails to come back needs someone physically at the machine. Use them when you can watch the result, not blind.
      </p>
      <p class="text-xs text-muted-foreground">
        {#if cabinet.agent_online}
          webMAN agent connected ({cabinet.agent_state === "game" ? "in game" : "on XMB"}). These keep working when the game is closed, which is
          what makes an unattended plugin update possible.
        {:else}
          Agent offline — the console is powered down, or the VSH plugin is not running. These will fail until it polls again.
        {/if}
      </p>

      <div class="flex flex-wrap items-center gap-2">
        {#each Object.keys(WEBMAN_LABELS) as action (action)}
          <Button
            variant="outline"
            size="sm"
            class="border-destructive/40 text-destructive hover:bg-destructive/10"
            disabled={!cabinet.agent_online || busy}
            onclick={() => { error = ""; pending = action as DangerAction; }}
          >
            {WEBMAN_LABELS[action as DangerAction].label}
          </Button>
        {/each}
      </div>
    </div>
  </details>

  <AlertDialog.Root open={pending !== null} onOpenChange={(isOpen) => { if (!isOpen) pending = null; }}>
    <AlertDialog.Content>
      {#if pending}
        <AlertDialog.Header>
          <AlertDialog.Title>{WEBMAN_LABELS[pending].label} on {cabinet.name || cabinet.cabinet_id}?</AlertDialog.Title>
          <AlertDialog.Description>{WEBMAN_LABELS[pending].confirm}</AlertDialog.Description>
        </AlertDialog.Header>
        {#if error}<p class="text-sm text-destructive">{error}</p>{/if}
        <AlertDialog.Footer>
          <AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
          <AlertDialog.Action variant="destructive" disabled={busy} onclick={() => runWebman(pending!)}>
            {busy ? "Sending…" : "Run it"}
          </AlertDialog.Action>
        </AlertDialog.Footer>
      {/if}
    </AlertDialog.Content>
  </AlertDialog.Root>

  {#if notice}<p class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">{notice}</p>{/if}
</div>
