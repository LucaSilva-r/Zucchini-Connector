<script lang="ts">
  import LockKeyholeIcon from "@lucide/svelte/icons/lock-keyhole";
  import type { Snippet } from "svelte";
  import { onMount } from "svelte";
  import { getManagementStatus, unlockManagement } from "$lib/api.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";

  let { children, compact = false }: { children: Snippet; compact?: boolean } = $props();

  let loaded = $state(false);
  let configured = $state(true);
  let unlocked = $state(false);
  let pin = $state("");
  let submitting = $state(false);
  let error = $state("");

  async function refresh() {
    try {
      const status = await getManagementStatus();
      configured = status.configured;
      unlocked = status.unlocked;
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not check management access";
    } finally {
      loaded = true;
    }
  }

  async function submit() {
    submitting = true;
    error = "";
    try {
      const status = await unlockManagement(pin);
      unlocked = status.unlocked;
      configured = status.configured;
      pin = "";
      window.dispatchEvent(new Event("zucchini-management-auth"));
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not unlock management controls";
    } finally {
      submitting = false;
    }
  }

  onMount(() => {
    refresh();
    const sync = () => refresh();
    window.addEventListener("zucchini-management-auth", sync);
    return () => window.removeEventListener("zucchini-management-auth", sync);
  });
</script>

{#if loaded && unlocked}
  {@render children()}
{:else}
  <div class={compact ? "rounded-lg border border-dashed bg-muted/20 p-2" : "grid min-h-44 place-items-center rounded-lg border border-dashed bg-muted/20 p-5"}>
    <form class={compact ? "flex items-center gap-2" : "grid w-full max-w-sm gap-3"} onsubmit={(event) => { event.preventDefault(); submit(); }}>
      {#if !compact}
        <div class="text-center">
          <LockKeyholeIcon class="mx-auto mb-2 size-6 text-muted-foreground" />
          <h3 class="text-sm font-semibold">Management access required</h3>
          <p class="mt-1 text-xs text-muted-foreground">
            {configured ? "Enter the connector management PIN." : "Set CONNECTOR_MANAGEMENT_PIN on the connector first."}
          </p>
        </div>
      {/if}
      {#if configured}
        <div class={compact ? "" : "grid gap-1.5"}>
          {#if !compact}<Label for="management-pin">Management PIN</Label>{/if}
          <Input id="management-pin" class={compact ? "h-8 w-28" : ""} placeholder={compact ? "PIN" : ""} aria-label={compact ? "Management PIN" : undefined} type="password" inputmode="numeric" autocomplete="current-password" bind:value={pin} />
        </div>
        <Button type="submit" size={compact ? "sm" : "default"} disabled={submitting || !pin}>{submitting ? "Unlocking…" : "Unlock"}</Button>
      {:else if compact}
        <span class="text-xs text-muted-foreground">Management PIN not configured</span>
      {/if}
      {#if error}<p class={compact ? "max-w-36 text-xs text-destructive" : "text-center text-xs text-destructive"}>{error}</p>{/if}
    </form>
  </div>
{/if}
