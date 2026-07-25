<script lang="ts">
  import LockKeyholeIcon from "@lucide/svelte/icons/lock-keyhole";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { cancelPrompt, management, submitPin } from "$lib/management.svelte.js";

  let pin = $state("");
  let submitting = $state(false);
  let error = $state("");

  async function submit(event: Event) {
    event.preventDefault();
    submitting = true;
    error = "";
    try {
      if (!(await submitPin(pin))) error = "Management is locked.";
      else pin = "";
    } catch (reason) {
      error = reason instanceof Error ? reason.message : "Could not unlock management controls";
    } finally {
      submitting = false;
    }
  }

  function dismiss() {
    pin = "";
    error = "";
    cancelPrompt();
  }
</script>

<AlertDialog.Root open={management.prompting} onOpenChange={(open) => { if (!open) dismiss(); }}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title class="flex items-center gap-2"><LockKeyholeIcon class="size-4" /> Management PIN</AlertDialog.Title>
      <AlertDialog.Description>
        {management.configured
          ? "This action changes the connector. Enter the management PIN to continue; it stays unlocked for the rest of the session."
          : "Set CONNECTOR_MANAGEMENT_PIN on the connector before using management actions."}
      </AlertDialog.Description>
    </AlertDialog.Header>
    {#if management.configured}
      <form class="grid gap-1.5" onsubmit={submit}>
        <Label for="management-pin">PIN</Label>
        <!-- svelte-ignore a11y_autofocus -->
        <Input id="management-pin" type="password" inputmode="numeric" autocomplete="current-password" autofocus bind:value={pin} />
        {#if error}<p class="text-xs text-destructive">{error}</p>{/if}
        <!-- Plain buttons: AlertDialog's own Action/Cancel close the dialog on
             click, which would tear the form down before it submits. -->
        <div class="mt-2 flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={submitting} onclick={dismiss}>Cancel</Button>
          <Button type="submit" disabled={submitting || !pin}>{submitting ? "Unlocking…" : "Unlock"}</Button>
        </div>
      </form>
    {:else}
      <div class="flex justify-end"><Button variant="outline" onclick={dismiss}>Close</Button></div>
    {/if}
  </AlertDialog.Content>
</AlertDialog.Root>
