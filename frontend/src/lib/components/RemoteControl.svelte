<script lang="ts">
  import Gamepad2Icon from "@lucide/svelte/icons/gamepad-2";
  import PowerIcon from "@lucide/svelte/icons/power";
  import RadioIcon from "@lucide/svelte/icons/radio";
  import CameraIcon from "@lucide/svelte/icons/camera";
  import ThermometerIcon from "@lucide/svelte/icons/thermometer";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { captureScreenshot, exitCabinetGame, screenshotUrl } from "$lib/api.js";
  import type { Cabinet } from "$lib/types.js";

  let { token, cabinet }: { token: string; cabinet: Cabinet } = $props();

  type ButtonName =
    | "hit_side_left" | "hit_center_left" | "hit_center_right" | "hit_side_right"
    | "enter" | "service" | "test" | "coin" | "up" | "down"
    | "p2_hit_side_left" | "p2_hit_center_left" | "p2_hit_center_right" | "p2_hit_side_right";

  // Keyed by KeyboardEvent.code so the drum keeps its physical layout on
  // non-QWERTY keyboards. These are captured while this panel is mounted,
  // which is only while the Control tab is open.
  const KEY_MAP: Record<string, ButtonName> = {
    KeyD: "hit_side_left", KeyF: "hit_center_left",
    KeyJ: "hit_center_right", KeyK: "hit_side_right",
    KeyZ: "p2_hit_side_left", KeyX: "p2_hit_center_left",
    KeyC: "p2_hit_center_right", KeyV: "p2_hit_side_right",
    Enter: "enter", NumpadEnter: "enter",
    ArrowUp: "up", ArrowDown: "down",
    F1: "test", F2: "service", F3: "coin",
  };
  const KEY_HINT: Partial<Record<ButtonName, string>> = {
    hit_side_left: "D", hit_center_left: "F", hit_center_right: "J", hit_side_right: "K",
    p2_hit_side_left: "Z", p2_hit_center_left: "X", p2_hit_center_right: "C", p2_hit_side_right: "V",
    enter: "Enter", up: "↑", down: "↓", test: "F1", service: "F2", coin: "F3",
  };

  let socket = $state<WebSocket | null>(null);
  let connected = $state(false);
  // Derived from the dashboard's live cabinet object, which the status stream
  // refreshes on every frame the cabinet sends. The control socket's one-shot
  // "cabinet" event only fires on connect and disconnect, so a reconnect could
  // leave this panel claiming the cabinet was offline indefinitely.
  const cabinetOnline = $derived(cabinet.control_online);
  let error = $state("");
  let exitOpen = $state(false);
  let exiting = $state(false);
  let exitError = $state("");
  let exitNotice = $state("");
  let held = $state<Set<ButtonName>>(new Set());
  let seq = 0;
  let reconnectTimer: number | undefined;
  let heartbeatTimer: number | undefined;
  let stopped = false;
  const controlCabinetId = $derived(cabinet.cabinet_id);

  function socketUrl(id: string) {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${location.host}/api/ui/cabinets/${encodeURIComponent(id)}/control`;
  }

  function sendState() {
    const ws = socket;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    seq = seq >= 0x7ffffffe ? 1 : seq + 1;
    ws.send(JSON.stringify({ type: "state", seq, buttons: [...held] }));
  }

  function setHeld(button: ButtonName, down: boolean) {
    const next = new Set(held);
    if (down) next.add(button);
    else next.delete(button);
    held = next;
    sendState();
  }

  function releaseAll() {
    if (!held.size) return;
    held = new Set();
    sendState();
  }

  function connect(id: string) {
    if (stopped) return;
    const ws = new WebSocket(socketUrl(id));
    socket = ws;
    ws.onopen = () => {
      if (socket !== ws) return;
      connected = true;
      error = "";
      sendState();
      window.clearInterval(heartbeatTimer);
      heartbeatTimer = window.setInterval(sendState, 200);
    };
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data));
        if (message.type === "error") error = String(message.message || "Control error");
      } catch {
        error = "Connector sent an invalid control message";
      }
    };
    ws.onerror = () => {
      if (socket === ws) error = "Remote-control connection failed";
    };
    ws.onclose = () => {
      if (socket !== ws) return;
      connected = false;
      socket = null;
      held = new Set();
      window.clearInterval(heartbeatTimer);
      if (!stopped) reconnectTimer = window.setTimeout(() => connect(id), 2000);
    };
  }

  function bindButton(button: ButtonName) {
    return {
      onpointerdown: (event: PointerEvent) => {
        event.preventDefault();
        (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
        setHeld(button, true);
      },
      onpointerup: (event: PointerEvent) => {
        event.preventDefault();
        setHeld(button, false);
      },
      onpointercancel: () => setHeld(button, false),
      onlostpointercapture: () => setHeld(button, false),
      oncontextmenu: (event: MouseEvent) => event.preventDefault(),
    };
  }

  function mappedButton(event: KeyboardEvent): ButtonName | null {
    // Leave browser shortcuts (Ctrl+F, Alt+…) and typing alone.
    if (event.ctrlKey || event.altKey || event.metaKey) return null;
    const target = event.target as HTMLElement | null;
    if (target?.isContentEditable) return null;
    if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return null;
    return KEY_MAP[event.code] ?? null;
  }

  function onKeyDown(event: KeyboardEvent) {
    const button = mappedButton(event);
    if (!button) return;
    // F1 opens help, F3 opens find, Space/Enter would click the focused
    // button, arrows scroll the page: all of it has to be swallowed here.
    event.preventDefault();
    if (event.repeat || held.has(button)) return;
    setHeld(button, true);
  }

  function onKeyUp(event: KeyboardEvent) {
    const button = KEY_MAP[event.code];
    if (!button) return;
    event.preventDefault();
    setHeld(button, false);
  }

  // Either half can capture: the plugin while the game runs, webMAN once it
  // has exited. Between them there is no state that cannot be seen.
  const canCapture = $derived(cabinet.agent_online || cabinet.control_online);
  // Only the one figure worth interrupting a control session for; the rest
  // lives on the Console tab. 80 C is where webMAN's own dynamic fan control
  // is already at full tilt.
  const hotCabinet = $derived(
    Math.max(cabinet.agent_health?.cpu_temp ?? 0, cabinet.agent_health?.rsx_temp ?? 0) >= 80,
  );
  let shotBusy = $state(false);
  let shotError = $state("");
  // Cache-busting stamp: the screenshot URL is fixed per cabinet, so the
  // browser would otherwise show the previous capture forever.
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

  async function closeGame() {
    exiting = true;
    exitError = "";
    try {
      await exitCabinetGame(token, cabinet.cabinet_id);
      exitNotice = "Close requested. The cabinet is on XMB — the drum still works there, so relaunch the game with it.";
      exitOpen = false;
    } catch (err) {
      exitError = err instanceof Error ? err.message : String(err);
    } finally {
      exiting = false;
    }
  }

  $effect(() => {
    const id = controlCabinetId;
    stopped = false;
    connect(id);
    const release = () => releaseAll();
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", release);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      stopped = true;
      window.clearTimeout(reconnectTimer);
      window.clearInterval(heartbeatTimer);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", release);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      releaseAll();
      socket?.close(1000, "Controller closed");
      socket = null;
    };
  });
</script>

<div class="grid gap-4">
  <div class="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 p-3">
    <div>
      <div class="flex items-center gap-2 text-sm font-semibold"><Gamepad2Icon class="size-4" /> Remote cabinet controls</div>
      <p class="mt-1 text-xs text-muted-foreground">Keyboard: D F J K (P1), Z X C V (P2), arrows, Enter, F1 test, F2 service, F3 coin. Controls are released automatically if this page or the cabinet disconnects.</p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <Badge variant={connected ? "outline" : "secondary"}><RadioIcon /> {connected ? "Relay connected" : "Connecting"}</Badge>
      <Badge variant={cabinetOnline ? "default" : "secondary"} class={cabinetOnline ? "bg-emerald-600 hover:bg-emerald-600" : ""}>
        {cabinetOnline ? "Cabinet connected" : "Cabinet offline"}
      </Badge>
      {#if cabinet.agent_online}
        <Badge variant="outline">Console {cabinet.agent_state === "game" ? "in game" : "on XMB"}</Badge>
      {/if}
      {#if hotCabinet}
        <!-- A cabinet cooking itself is worth interrupting for, so the warning
             stays here even though the readings themselves are a tab away. -->
        <Badge variant="outline" class="border-destructive/50 text-destructive">
          <ThermometerIcon /> {cabinet.agent_health.cpu_temp}°C — see Console tab
        </Badge>
      {/if}
      <Button variant="outline" size="sm" disabled={!canCapture || shotBusy} onclick={takeScreenshot}>
        <CameraIcon /> {shotBusy ? "Capturing…" : "Screenshot"}
      </Button>
      <Button variant="outline" size="sm" class="border-destructive/40 text-destructive hover:bg-destructive/10" disabled={!cabinetOnline || exiting} onclick={() => { exitError = ""; exitOpen = true; }}>
        <PowerIcon /> Close game
      </Button>
    </div>
  </div>

  <AlertDialog.Root bind:open={exitOpen}>
    <AlertDialog.Content>
      <AlertDialog.Header>
        <AlertDialog.Title>Close the game on {cabinet.name || cabinet.cabinet_id}?</AlertDialog.Title>
        <AlertDialog.Description>
          This ends the running game immediately — any credit or play in progress is lost. The cabinet drops to the PS3 XMB, where it stops
          answering this connector until the game runs again. The drum still acts as a controller on XMB, so relaunch the game with it from
          this page's controls. Do this only when you can watch the screen: nothing else can bring the cabinet back.
        </AlertDialog.Description>
      </AlertDialog.Header>
      {#if exitError}<p class="text-sm text-destructive">{exitError}</p>{/if}
      <AlertDialog.Footer>
        <AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
        <AlertDialog.Action variant="destructive" disabled={exiting} onclick={closeGame}>
          {exiting ? "Closing…" : "Close the game"}
        </AlertDialog.Action>
      </AlertDialog.Footer>
    </AlertDialog.Content>
  </AlertDialog.Root>

  {#if shotError}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{shotError}</p>{/if}
  {#if shotUrl}
    <div class="grid gap-1">
      <img src={shotUrl} alt="Console screen" class="w-full max-w-2xl rounded-md border" />
      <p class="text-xs text-muted-foreground">
        Captured by the plugin while the game runs, or by the webMAN agent on XMB — whichever is up.
      </p>
    </div>
  {/if}

  {#if exitNotice}<p class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">{exitNotice}</p>{/if}
  {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}

{#snippet control(name: ButtonName, label: string, extra: string, variant: "outline" | "default" = "outline")}
  <Button {variant} class={`touch-none ${extra} ${held.has(name) ? "ring-2 ring-primary" : ""}`} {...bindButton(name)}>
    <span class="flex flex-col items-center leading-tight">
      <span>{label}</span>
      {#if KEY_HINT[name]}<span class="font-mono text-[10px] opacity-60">{KEY_HINT[name]}</span>{/if}
    </span>
  </Button>
{/snippet}

  <div class="grid select-none gap-5 md:grid-cols-2">
    <section class="grid gap-3 rounded-lg border p-4">
      <h3 class="text-sm font-semibold">Operator panel</h3>
      <div class="mx-auto grid w-full max-w-xs grid-cols-3 gap-2">
        <span></span>
        {@render control("up", "Up", "h-14")}
        <span></span>
        {@render control("service", "Service", "h-14")}
        {@render control("enter", "Enter", "h-14", "default")}
        {@render control("test", "Test", "h-14")}
        <span></span>
        {@render control("down", "Down", "h-14")}
        {@render control("coin", "Coin", "h-14")}
      </div>
    </section>

    <section class="grid gap-3 rounded-lg border p-4">
      <h3 class="text-sm font-semibold">P1 drum</h3>
      <div class="grid grid-cols-4 gap-2">
        {@render control("hit_side_left", "Left rim", "h-24 border-blue-500/50 bg-blue-500/10")}
        {@render control("hit_center_left", "Left center", "h-24 border-red-500/50 bg-red-500/10")}
        {@render control("hit_center_right", "Right center", "h-24 border-red-500/50 bg-red-500/10")}
        {@render control("hit_side_right", "Right rim", "h-24 border-blue-500/50 bg-blue-500/10")}
      </div>
    </section>

    <section class="grid gap-3 rounded-lg border p-4 md:col-start-2">
      <h3 class="text-sm font-semibold">P2 drum</h3>
      <div class="grid grid-cols-4 gap-2">
        {@render control("p2_hit_side_left", "Left rim", "h-24 border-blue-500/50 bg-blue-500/10")}
        {@render control("p2_hit_center_left", "Left center", "h-24 border-red-500/50 bg-red-500/10")}
        {@render control("p2_hit_center_right", "Right center", "h-24 border-red-500/50 bg-red-500/10")}
        {@render control("p2_hit_side_right", "Right rim", "h-24 border-blue-500/50 bg-blue-500/10")}
      </div>
    </section>
  </div>
</div>
