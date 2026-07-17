<script lang="ts">
  import LoaderCircleIcon from "@lucide/svelte/icons/loader-circle";
  import SaveIcon from "@lucide/svelte/icons/save";
  import { saveConfig } from "$lib/api.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Switch } from "$lib/components/ui/switch/index.js";
  import { Textarea } from "$lib/components/ui/textarea/index.js";
  import type { Cabinet } from "$lib/types.js";

  let { token, cabinet, onSaved }: {
    token: string;
    cabinet: Cabinet;
    onSaved: (cabinet: Cabinet) => void;
  } = $props();

  let flags = $state<Record<string, boolean>>({});
  let flagsDirty = $state(false);
  let configText = $state("");
  let savingFlags = $state(false);
  let savingConfig = $state(false);
  let error = $state("");
  let loadedCabinetId = $state("");
  let loadedReport = $state("");

  function parseChassis(raw: string) {
    const result: Record<string, boolean> = {};
    let inSection = false;
    for (const rawLine of raw.split("\n")) {
      const line = rawLine.trim();
      if (line.startsWith("[")) {
        inSection = line.toLocaleLowerCase() === "[chassis]";
        continue;
      }
      if (!inSection || !line || line.startsWith("#") || !line.includes("=")) continue;
      const [key, value] = line.split("=", 2).map((part) => part.trim());
      if (key) result[key] = value !== "0";
    }
    return result;
  }

  $effect(() => {
    if (loadedCabinetId !== cabinet.cabinet_id || (!flagsDirty && loadedReport !== cabinet.reported_cfg)) {
      const reported = parseChassis(cabinet.reported_cfg);
      for (const [key, value] of Object.entries(cabinet.config_pending)) {
        if (key.startsWith("chassis.")) reported[key.slice(8)] = value !== "0";
      }
      flags = reported;
      loadedCabinetId = cabinet.cabinet_id;
      loadedReport = cabinet.reported_cfg;
      flagsDirty = false;
    }
  });

  function reportedFlags() {
    return parseChassis(cabinet.reported_cfg);
  }

  async function persistFlags() {
    const reported = reportedFlags();
    const config: Record<string, string> = {};
    for (const [key, value] of Object.entries(flags)) {
      if (reported[key] !== value) config[`chassis.${key}`] = value ? "1" : "0";
    }
    if (!Object.keys(config).length) return;
    savingFlags = true;
    error = "";
    try {
      onSaved(await saveConfig(token, cabinet.cabinet_id, config));
      flagsDirty = false;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not queue flags";
    } finally {
      savingFlags = false;
    }
  }

  async function persistConfig() {
    const config: Record<string, string> = {};
    for (const line of configText.split("\n")) {
      const match = line.match(/^\s*([\w.]+)\s*=\s*(.*?)\s*$/);
      if (match) config[match[1]] = match[2];
    }
    if (!Object.keys(config).length) return;
    savingConfig = true;
    error = "";
    try {
      onSaved(await saveConfig(token, cabinet.cabinet_id, config));
      configText = "";
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not queue configuration";
    } finally {
      savingConfig = false;
    }
  }
</script>

<div class="grid gap-4">
  {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}

  <section class="grid gap-3">
    <div class="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h3 class="text-sm font-semibold">Operator flags</h3>
        <p class="text-xs text-muted-foreground">Applied at the next cabinet poll and read by the game during boot.</p>
      </div>
      <Button size="sm" disabled={!flagsDirty || savingFlags} onclick={persistFlags}>
        {#if savingFlags}<LoaderCircleIcon class="animate-spin" />{:else}<SaveIcon />{/if}
        Queue changes
      </Button>
    </div>

    {#if Object.keys(flags).length === 0}
      <div class="rounded-lg border border-dashed py-10 text-center text-sm text-muted-foreground">Waiting for a reported chassis section.</div>
    {:else}
      <div class="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {#each Object.entries(flags) as [name, enabled] (name)}
          <Label class="flex items-center justify-between gap-3 rounded-md border bg-background/60 px-2.5 py-1.5 font-normal">
            <span class="min-w-0 truncate font-mono text-xs">{name}</span>
            <span class="flex items-center gap-2">
              {#if cabinet.config_pending[`chassis.${name}`] !== undefined}<Badge variant="outline" class="text-amber-700 dark:text-amber-400">Pending</Badge>{/if}
              <Switch checked={enabled} onCheckedChange={(value) => { flags = { ...flags, [name]: value }; flagsDirty = true; }} />
            </span>
          </Label>
        {/each}
      </div>
    {/if}
  </section>

  <section class="grid gap-2 border-t pt-4">
    <div>
      <h3 class="text-sm font-semibold">Advanced configuration</h3>
      <p class="text-xs text-muted-foreground">Enter one <code class="rounded bg-muted px-1.5 py-0.5 text-xs">section.key = value</code> per line.</p>
    </div>
    <Textarea bind:value={configText} rows={4} class="font-mono text-xs" placeholder="network.connector_port = 8443" />
    <div><Button variant="secondary" size="sm" disabled={!configText.trim() || savingConfig} onclick={persistConfig}>{savingConfig ? "Queuing…" : "Queue configuration"}</Button></div>
  </section>

  {#if Object.keys(cabinet.config_pending).length > 0}
    <section class="grid gap-2 border-t pt-4">
      <h3 class="text-sm font-semibold">Pending delivery</h3>
      <div class="flex flex-wrap gap-2">
        {#each Object.entries(cabinet.config_pending) as [key, value]}
          <Badge variant="outline" class="font-mono">{key}={value}</Badge>
        {/each}
      </div>
    </section>
  {/if}

  <details class="rounded-lg border bg-muted/30 px-4 py-3">
    <summary class="cursor-pointer text-sm font-medium">Reported taiko_config.cfg</summary>
    <pre class="mt-3 max-h-96 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{cabinet.reported_cfg || "Not reported yet."}</pre>
  </details>
</div>
