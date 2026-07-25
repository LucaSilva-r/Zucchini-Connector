/**
 * Management-PIN access, shared by every privileged action in the UI.
 *
 * Actions call `ensureManagement()` and only proceed once it resolves true.
 * That opens the one global PIN dialog when the session is still locked, so
 * the PIN is asked for at the moment it is needed instead of sitting in a
 * form next to every control.
 */
import { getManagementStatus, lockManagement, unlockManagement } from "$lib/api.js";

let configured = $state(true);
let unlocked = $state(false);
let pending = $state<((granted: boolean) => void) | null>(null);

export const management = {
  get configured() { return configured; },
  get unlocked() { return unlocked; },
  get prompting() { return pending !== null; },
};

export async function refreshManagement(): Promise<void> {
  try {
    const status = await getManagementStatus();
    configured = status.configured;
    unlocked = status.unlocked;
  } catch {
    // Leave the last known state; the action itself will surface the error.
  }
}

export function ensureManagement(): Promise<boolean> {
  if (unlocked) return Promise.resolve(true);
  // A second request while the dialog is open replaces the first, which would
  // strand its caller, so refuse it instead.
  if (pending) return Promise.resolve(false);
  return new Promise((resolve) => (pending = resolve));
}

export async function submitPin(pin: string): Promise<boolean> {
  const status = await unlockManagement(pin);
  configured = status.configured;
  unlocked = status.unlocked;
  if (unlocked) settle(true);
  return unlocked;
}

export async function lock(): Promise<void> {
  await lockManagement();
  unlocked = false;
}

export function cancelPrompt(): void {
  settle(false);
}

function settle(granted: boolean): void {
  const resolve = pending;
  pending = null;
  resolve?.(granted);
}
