<script lang="ts">
  import ThermometerIcon from "@lucide/svelte/icons/thermometer";
  import type { Cabinet } from "$lib/types.js";

  let { cabinet }: { cabinet: Cabinet } = $props();

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
</div>
