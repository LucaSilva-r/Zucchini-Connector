<script lang="ts">
  import ThermometerIcon from "@lucide/svelte/icons/thermometer";
  import { runWebmanAction, type WebmanAction } from "$lib/api.js";
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
  const WEBMAN_LABELS: Record<WebmanAction, { label: string; confirm: string }> = {
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
  let pending = $state<WebmanAction | null>(null);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let open = $state(false);

  async function runWebman(action: WebmanAction) {
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
            onclick={() => { error = ""; pending = action as WebmanAction; }}
          >
            {WEBMAN_LABELS[action as WebmanAction].label}
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
