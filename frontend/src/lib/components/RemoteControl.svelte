<script lang="ts">
  import Gamepad2Icon from "@lucide/svelte/icons/gamepad-2";
  import RadioIcon from "@lucide/svelte/icons/radio";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import type { Cabinet } from "$lib/types.js";

  let { cabinet }: { token: string; cabinet: Cabinet } = $props();

  type ButtonName =
    | "hit_side_left" | "hit_center_left" | "hit_center_right" | "hit_side_right"
    | "enter" | "service" | "test" | "coin" | "up" | "down"
    | "p2_hit_side_left" | "p2_hit_center_left" | "p2_hit_center_right" | "p2_hit_side_right";

  let socket = $state<WebSocket | null>(null);
  let connected = $state(false);
  let cabinetOnline = $state(false);
  let error = $state("");
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
        if (message.type === "cabinet") cabinetOnline = Boolean(message.online);
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
      cabinetOnline = false;
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

  $effect(() => {
    const id = controlCabinetId;
    stopped = false;
    connect(id);
    const release = () => releaseAll();
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", release);
    return () => {
      stopped = true;
      window.clearTimeout(reconnectTimer);
      window.clearInterval(heartbeatTimer);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", release);
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
      <p class="mt-1 text-xs text-muted-foreground">Controls are released automatically if this page or the cabinet disconnects.</p>
    </div>
    <div class="flex gap-2">
      <Badge variant={connected ? "outline" : "secondary"}><RadioIcon /> {connected ? "Relay connected" : "Connecting"}</Badge>
      <Badge variant={cabinetOnline ? "default" : "secondary"} class={cabinetOnline ? "bg-emerald-600 hover:bg-emerald-600" : ""}>
        {cabinetOnline ? "Cabinet connected" : "Cabinet offline"}
      </Badge>
    </div>
  </div>

  {#if error}<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>{/if}

  <div class="grid select-none gap-5 md:grid-cols-2">
    <section class="grid gap-3 rounded-lg border p-4">
      <h3 class="text-sm font-semibold">Operator panel</h3>
      <div class="mx-auto grid w-full max-w-xs grid-cols-3 gap-2">
        <span></span>
        <Button variant="outline" class="h-14 touch-none" {...bindButton("up")}>Up</Button>
        <span></span>
        <Button variant="outline" class="h-14 touch-none" {...bindButton("service")}>Service</Button>
        <Button class="h-14 touch-none" {...bindButton("enter")}>Enter</Button>
        <Button variant="outline" class="h-14 touch-none" {...bindButton("test")}>Test</Button>
        <span></span>
        <Button variant="outline" class="h-14 touch-none" {...bindButton("down")}>Down</Button>
        <Button variant="outline" class="h-14 touch-none" {...bindButton("coin")}>Coin</Button>
      </div>
    </section>

    <section class="grid gap-3 rounded-lg border p-4">
      <h3 class="text-sm font-semibold">P1 drum</h3>
      <div class="grid grid-cols-4 gap-2">
        <Button variant="outline" class="h-24 touch-none border-blue-500/50 bg-blue-500/10" {...bindButton("hit_side_left")}>Left rim</Button>
        <Button variant="outline" class="h-24 touch-none border-red-500/50 bg-red-500/10" {...bindButton("hit_center_left")}>Left center</Button>
        <Button variant="outline" class="h-24 touch-none border-red-500/50 bg-red-500/10" {...bindButton("hit_center_right")}>Right center</Button>
        <Button variant="outline" class="h-24 touch-none border-blue-500/50 bg-blue-500/10" {...bindButton("hit_side_right")}>Right rim</Button>
      </div>
    </section>

    <section class="grid gap-3 rounded-lg border p-4 md:col-start-2">
      <h3 class="text-sm font-semibold">P2 drum</h3>
      <div class="grid grid-cols-4 gap-2">
        <Button variant="outline" class="h-24 touch-none border-blue-500/50 bg-blue-500/10" {...bindButton("p2_hit_side_left")}>Left rim</Button>
        <Button variant="outline" class="h-24 touch-none border-red-500/50 bg-red-500/10" {...bindButton("p2_hit_center_left")}>Left center</Button>
        <Button variant="outline" class="h-24 touch-none border-red-500/50 bg-red-500/10" {...bindButton("p2_hit_center_right")}>Right center</Button>
        <Button variant="outline" class="h-24 touch-none border-blue-500/50 bg-blue-500/10" {...bindButton("p2_hit_side_right")}>Right rim</Button>
      </div>
    </section>
  </div>
</div>
