/** Accès à l'API du serveur Atlas (même origine, cookie de session HttpOnly). */

export class ApiError extends Error {
  status: number;
  code: string | null;
  data: unknown;
  constructor(status: number, message: string, code: string | null = null, data: unknown = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

let onAuthLost: (() => void) | null = null;
export function setAuthLostHandler(fn: () => void) {
  onAuthLost = fn;
}

async function parseError(resp: Response): Promise<ApiError> {
  let message = `Erreur ${resp.status}`;
  let code: string | null = null;
  let data: unknown = null;
  try {
    const body = await resp.json();
    data = body;
    const detail = body && body.detail;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object") {
      message = detail.message || message;
      code = detail.code || null;
    } else if (Array.isArray(detail)) message = "Données invalides.";
    if (body && body.code) code = body.code;
  } catch {
    /* corps non JSON */
  }
  return new ApiError(resp.status, message, code, data);
}

export async function api<T = unknown>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const init: RequestInit = { credentials: "same-origin", ...options };
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  }
  init.headers = headers;
  let resp: Response;
  try {
    resp = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Serveur Atlas injoignable. Vérifie que le PC Windows est allumé et qu'Atlas est lancé.", "network");
  }
  if (!resp.ok) {
    const err = await parseError(resp);
    if (resp.status === 401 && err.code === "auth_required" && onAuthLost) onAuthLost();
    throw err;
  }
  const ctype = resp.headers.get("content-type") || "";
  if (ctype.indexOf("application/json") >= 0) return (await resp.json()) as T;
  return (await resp.blob()) as unknown as T;
}

export function randomId(prefix: string): string {
  const bytes = new Uint8Array(12);
  (window.crypto || (window as any).msCrypto).getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += ("0" + bytes[i].toString(16)).slice(-2);
  return `${prefix}_${out}`;
}

/** Identifiant stable de cet appareil/navigateur (sert à jouer la voix uniquement ici). */
export function deviceId(): string {
  const key = "atlas.deviceId";
  try {
    let id = localStorage.getItem(key);
    if (!id) {
      id = randomId("dev");
      localStorage.setItem(key, id);
    }
    return id;
  } catch {
    const w = window as any;
    if (!w.__atlasDevice) w.__atlasDevice = randomId("dev");
    return w.__atlasDevice;
  }
}

export function localPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem("atlas." + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function setLocalPref(key: string, value: unknown) {
  try {
    localStorage.setItem("atlas." + key, JSON.stringify(value));
  } catch {
    /* stockage indisponible : préférence non mémorisée */
  }
}
