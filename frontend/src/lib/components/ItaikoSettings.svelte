<script lang="ts">
  import LoaderCircleIcon from "@lucide/svelte/icons/loader-circle";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import SaveIcon from "@lucide/svelte/icons/save";
  import { readItaikoSettings, saveItaikoSettings } from "$lib/api.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Switch } from "$lib/components/ui/switch/index.js";
  import type { Cabinet } from "$lib/types.js";

  let { token, cabinet }: { token: string; cabinet: Cabinet } = $props();

  const pads = [
    { name: "Don left", light: 0, heavy: 10, cutoff: 14 },
    { name: "Ka left", light: 1, heavy: 11, cutoff: 15 },
    { name: "Don right", light: 2, heavy: 12, cutoff: 16 },
    { name: "Ka right", light: 3, heavy: 13, cutoff: 17 },
  ];
  const timings = [
    { key: 4, name: "Don debounce", help: "Minimum delay between center hits", max: 1000 },
    { key: 5, name: "Ka debounce", help: "Minimum delay between rim hits", max: 1000 },
    { key: 6, name: "Crosstalk suppression", help: "Reject nearby opposite-pad hits", max: 1000 },
    { key: 7, name: "Per-sensor debounce", help: "Minimum delay on the same sensor", max: 1000 },
    { key: 8, name: "Key hold", help: "Keyboard press duration", max: 1000 },
    { key: 46, name: "Roll boost", help: "Extra hold time during drum rolls", max: 50 },
  ];

  let draft = $state<Record<string, number>>({});
  let dirty = $state(false);
  let loading = $state(false);
  let saving = $state(false);
  let error = $state("");
  let lastSnapshot = "";

  $effect(() => {
    const state = cabinet.itaiko.state;
    const snapshot = JSON.stringify(cabinet.itaiko.settings);
    if (snapshot !== lastSnapshot && Object.keys(cabinet.itaiko.settings).length) {
      draft = { ...cabinet.itaiko.settings };
      lastSnapshot = snapshot;
      dirty = false;
    }
    if (state === "ready" || state === "error" || state === "disconnected") {
      loading = false;
      saving = false;
    }
  });

  function setValue(key: number, value: number, maximum: number) {
    if (!Number.isFinite(value)) return;
    draft = { ...draft, [String(key)]: Math.max(0, Math.min(maximum, Math.round(value))) };
    dirty = true;
  }

  function inputValue(key: number, maximum: number, event: Event) {
    setValue(key, (event.currentTarget as HTMLInputElement).valueAsNumber, maximum);
  }

  async function refresh() {
    loading = true;
    error = "";
    try {
      await readItaikoSettings(token, cabinet.cabinet_id);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not read ITAIKO settings";
      loading = false;
    }
  }

  async function save() {
    saving = true;
    error = "";
    try {
      await saveItaikoSettings(token, cabinet.cabinet_id, draft);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not save ITAIKO settings";
      saving = false;
    }
  }

  const usable = $derived(cabinet.control_online && Object.keys(draft).length > 0);
  const busy = $derived(cabinet.itaiko.state === "busy" || loading || saving);
</script>

<div class="grid gap-5">
  <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div>
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="text-sm font-semibold">ITAIKO drum settings</h3>
        <Badge
          variant={cabinet.itaiko.state === "ready" ? "default" : "outline"}
          class={cabinet.itaiko.state === "ready" ? "bg-emerald-600 hover:bg-emerald-600" : ""}
        >
          {cabinet.itaiko.state === "ready" ? "Connected" : cabinet.itaiko.state}
        </Badge>
        {#if cabinet.itaiko.edition}<Badge variant="outline">{cabinet.itaiko.edition} {cabinet.itaiko.version}</Badge>{/if}
      </div>
      <p class="mt-1 text-xs text-muted-foreground">
        Changes are applied immediately and saved in the controller. Only sensitivities and timings are exposed here.
      </p>
    </div>
    <div class="flex gap-2">
      <Button variant="outline" size="sm" disabled={!cabinet.control_online || busy} onclick={refresh}>
        <RefreshCwIcon class={loading ? "animate-spin" : ""} /> Refresh
      </Button>
      <Button size="sm" disabled={!usable || !dirty || busy} onclick={save}>
        {#if saving}<LoaderCircleIcon class="animate-spin" />{:else}<SaveIcon />{/if}
        Save to drum
      </Button>
    </div>
  </div>

  {#if error || cabinet.itaiko.error}
    <p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {error || cabinet.itaiko.error}
    </p>
  {/if}

  {#if !Object.keys(draft).length}
    <div class="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">
      {cabinet.control_online ? "Waiting for ITAIKO settings…" : "The cabinet is offline."}
    </div>
  {:else}
    <section class="grid gap-3">
      <div>
        <h4 class="text-sm font-medium">Sensitivity</h4>
        <p class="text-xs text-muted-foreground">Higher thresholds require a harder hit.</p>
      </div>

      <Label class="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2 font-normal">
        <span>
          <span class="block text-sm font-medium">Double-input thresholds</span>
          <span class="block text-xs text-muted-foreground">Use separate light and heavy hit thresholds.</span>
        </span>
        <Switch
          checked={(draft["9"] ?? 0) !== 0}
          onCheckedChange={(checked) => setValue(9, checked ? 1 : 0, 1)}
          disabled={busy}
        />
      </Label>

      <div class="grid gap-3 lg:grid-cols-2">
        {#each pads as pad (pad.name)}
          <div class="grid gap-3 rounded-lg border p-3">
            <h5 class="text-sm font-medium">{pad.name}</h5>
            <div class="grid gap-1.5">
              <div class="flex items-center justify-between text-xs"><span>Light threshold</span><span class="font-mono">{draft[String(pad.light)]}</span></div>
              <div class="flex gap-3">
                <input class="min-w-0 flex-1 accent-primary" type="range" min="0" max="4095" value={draft[String(pad.light)]} disabled={busy} oninput={(event) => inputValue(pad.light, 4095, event)} />
                <Input class="w-24 font-mono" type="number" min="0" max="4095" value={draft[String(pad.light)]} disabled={busy} oninput={(event) => inputValue(pad.light, 4095, event)} />
              </div>
            </div>
            {#if (draft["9"] ?? 0) !== 0}
              <div class="grid gap-1.5">
                <div class="flex items-center justify-between text-xs"><span>Heavy threshold</span><span class="font-mono">{draft[String(pad.heavy)]}</span></div>
                <div class="flex gap-3">
                  <input class="min-w-0 flex-1 accent-primary" type="range" min="0" max="4095" value={draft[String(pad.heavy)]} disabled={busy} oninput={(event) => inputValue(pad.heavy, 4095, event)} />
                  <Input class="w-24 font-mono" type="number" min="0" max="4095" value={draft[String(pad.heavy)]} disabled={busy} oninput={(event) => inputValue(pad.heavy, 4095, event)} />
                </div>
              </div>
            {/if}
            <div class="grid gap-1.5">
              <div class="flex items-center justify-between text-xs"><span>Cutoff</span><span class="font-mono">{draft[String(pad.cutoff)]}</span></div>
              <div class="flex gap-3">
                <input class="min-w-0 flex-1 accent-primary" type="range" min="0" max="4095" value={draft[String(pad.cutoff)]} disabled={busy} oninput={(event) => inputValue(pad.cutoff, 4095, event)} />
                <Input class="w-24 font-mono" type="number" min="0" max="4095" value={draft[String(pad.cutoff)]} disabled={busy} oninput={(event) => inputValue(pad.cutoff, 4095, event)} />
              </div>
            </div>
          </div>
        {/each}
      </div>
    </section>

    <section class="grid gap-3 border-t pt-4">
      <div>
        <h4 class="text-sm font-medium">Timings</h4>
        <p class="text-xs text-muted-foreground">All values are milliseconds.</p>
      </div>
      <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {#each timings as timing (timing.key)}
          <Label class="grid gap-1 rounded-lg border p-3 font-normal">
            <span class="text-sm font-medium">{timing.name}</span>
            <span class="text-xs text-muted-foreground">{timing.help}</span>
            <Input class="mt-1 font-mono" type="number" min="0" max={timing.max} value={draft[String(timing.key)]} disabled={busy} oninput={(event) => inputValue(timing.key, timing.max, event)} />
          </Label>
        {/each}
      </div>
    </section>
  {/if}
</div>
