/** AudioContext partagé (créé à la demande, débloqué par un geste de l'utilisateur). */

let ctx: AudioContext | null = null;

export function getAudioContext(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor: typeof AudioContext | undefined = (window as any).AudioContext || (window as any).webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx = new Ctor();
  } catch {
    ctx = null;
  }
  return ctx;
}

/** À appeler dans un gestionnaire de clic : autorise la lecture audio ultérieure. */
export function unlockAudio(): void {
  const c = getAudioContext();
  if (!c) return;
  if (c.state === "suspended") {
    const p = c.resume();
    if (p && typeof p.catch === "function") p.catch(() => undefined);
  }
  // Safari ancien : jouer un tampon silencieux pendant le geste.
  try {
    const buffer = c.createBuffer(1, 1, 22050);
    const src = c.createBufferSource();
    src.buffer = buffer;
    src.connect(c.destination);
    src.start(0);
  } catch {
    /* sans importance */
  }
}

/** Niveau RMS (0..1) d'un analyseur, compatible Safari 12 (données en octets). */
export function analyserLevel(analyser: AnalyserNode, buffer: Uint8Array): number {
  analyser.getByteTimeDomainData(buffer);
  let sum = 0;
  for (let i = 0; i < buffer.length; i++) {
    const v = (buffer[i] - 128) / 128;
    sum += v * v;
  }
  return Math.min(1, Math.sqrt(sum / buffer.length) * 3.2);
}
