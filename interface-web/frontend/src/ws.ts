/** Connexion temps réel avec reconnexion progressive. Ne renvoie jamais d'action : lecture seule. */

export type WsStatus = "connecting" | "open" | "closed" | "auth";

export class RealtimeClient {
  private ws: WebSocket | null = null;
  private retry = 0;
  private timer: number | null = null;
  private pingTimer: number | null = null;
  private stopped = false;
  constructor(
    private onMessage: (msg: any) => void,
    private onStatus: (s: WsStatus) => void,
  ) {}

  start() {
    this.stopped = false;
    this.connect();
    document.addEventListener("visibilitychange", this.onVisibility);
  }

  stop() {
    this.stopped = true;
    document.removeEventListener("visibilitychange", this.onVisibility);
    if (this.timer) window.clearTimeout(this.timer);
    if (this.pingTimer) window.clearInterval(this.pingTimer);
    if (this.ws) this.ws.close();
  }

  private onVisibility = () => {
    if (!document.hidden && (!this.ws || this.ws.readyState === WebSocket.CLOSED)) {
      this.retry = 0;
      this.connect();
    }
  };

  private connect() {
    if (this.stopped) return;
    if (this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.onStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${proto}//${location.host}/api/ws`);
    } catch {
      this.schedule();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onStatus("open");
      if (this.pingTimer) window.clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 25000);
    };
    ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data));
      } catch {
        /* message illisible ignoré */
      }
    };
    ws.onclose = (ev) => {
      if (this.pingTimer) window.clearInterval(this.pingTimer);
      if (ev.code === 4401) {
        this.onStatus("auth");
        return;
      }
      this.onStatus("closed");
      this.schedule();
    };
  }

  private schedule() {
    if (this.stopped) return;
    const delay = Math.min(15000, 1000 * Math.pow(1.7, this.retry++));
    this.timer = window.setTimeout(() => this.connect(), document.hidden ? Math.max(delay, 10000) : delay);
  }

  reconnectNow() {
    this.retry = 0;
    if (this.ws) this.ws.close();
    this.connect();
  }
}
