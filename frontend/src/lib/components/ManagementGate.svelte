<script lang="ts">
  import LockKeyholeIcon from "@lucide/svelte/icons/lock-keyhole";
  import type { Snippet } from "svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { ensureManagement, management } from "$lib/management.svelte.js";

  // Panels that should not even mount while locked (they open sockets or show
  // cabinet internals). One-off buttons don't need this: they just call
  // ensureManagement() in their click handler.
  let { children }: { children: Snippet } = $props();
</script>

{#if management.unlocked}
  {@render children()}
{:else}
  <div class="grid min-h-44 place-items-center rounded-lg border border-dashed bg-muted/20 p-5 text-center">
    <div class="grid gap-2 justify-items-center">
      <LockKeyholeIcon class="size-6 text-muted-foreground" />
      <h3 class="text-sm font-semibold">Management access required</h3>
      <p class="max-w-sm text-xs text-muted-foreground">
        {management.configured
          ? "Unlock with the connector management PIN to use these controls."
          : "Set CONNECTOR_MANAGEMENT_PIN on the connector first."}
      </p>
      {#if management.configured}
        <Button size="sm" class="mt-1" onclick={() => ensureManagement()}>Unlock</Button>
      {/if}
    </div>
  </div>
{/if}
