/**
 * Enregistrement du micro en WAV PCM 16 bits mono 16 kHz.
 * Utilise Web Audio (ScriptProcessor) plutôt que MediaRecorder : fonctionne aussi
 * sur Safari 12/13 (macOS High Sierra), qui n'a pas MediaRecorder.
 * Aucun audio n'est envoyé ailleurs qu'au serveur Atlas.
 */
import { analyserLevel, getAudioContext } from "./context";

export type RecorderErrorKind = "insecure" | "unsupported" | "denied" | "nodevice" | "busy" | "unknown";

export class RecorderError extends Error {
  kind: RecorderErrorKind;
  constructor(kind: RecorderErrorKind, message: string) {
    super(message);
    this.kind = kind;
  }
}

export interface RecorderHandle {
  stop(): Promise<Blob>;
  cancel(): void;
  level(): number;
  seconds(): number;
}

export function micSupport(): { ok: boolean; kind?: RecorderErrorKind; message?: string } {
  if (window.isSecureContext === false) {
    return {
      ok: false,
      kind: "insecure",
      message:
        "Le micro exige une connexion sécurisée (HTTPS ou localhost). Active le mode réseau HTTPS d'Atlas — voir le guide d'accès depuis le Mac.",
    };
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return { ok: false, kind: "unsupported", message: "Ce navigateur ne permet pas l'accès au micro." };
  }
  if (!getAudioContext()) {
    return { ok: false, kind: "unsupported", message: "Ce navigateur ne gère pas l'audio Web." };
  }
  return { ok: true };
}

export async function listInputDevices(): Promise<{ id: string; label: string }[]> {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((d) => d.kind === "audioinput")
    .map((d, i) => ({ id: d.deviceId, label: d.label || `Micro ${i + 1}` }));
}

export function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

export function downsample(chunks: Float32Array[], inRate: number, outRate: number): Float32Array {
  let total = 0;
  for (const c of chunks) total += c.length;
  const merged = new Float32Array(total);
  let pos = 0;
  for (const c of chunks) {
    merged.set(c, pos);
    pos += c.length;
  }
  if (inRate === outRate) return merged;
  const ratio = inRate / outRate;
  const out = new Float32Array(Math.floor(merged.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(merged.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += merged[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

export async function startRecording(inputDeviceId?: string): Promise<RecorderHandle> {
  const support = micSupport();
  if (!support.ok) throw new RecorderError(support.kind!, support.message!);
  const ctx = getAudioContext()!;
  if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
  let stream: MediaStream;
  try {
    const audio: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (inputDeviceId) audio.deviceId = { exact: inputDeviceId };
    stream = await navigator.mediaDevices.getUserMedia({ audio });
  } catch (e: any) {
    const name = e && e.name;
    if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError")
      throw new RecorderError("denied", "Accès au micro refusé. Autorise-le dans les réglages du navigateur pour ce site.");
    if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError")
      throw new RecorderError("nodevice", "Aucun micro détecté (ou le micro choisi n'est plus branché).");
    if (name === "NotReadableError")
      throw new RecorderError("busy", "Le micro est utilisé par une autre application.");
    throw new RecorderError("unknown", "Impossible d'ouvrir le micro.");
  }
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  const levelBuf = new Uint8Array(analyser.fftSize);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const mute = ctx.createGain();
  mute.gain.value = 0;
  const chunks: Float32Array[] = [];
  const startedAt = Date.now();
  processor.onaudioprocess = (ev) => {
    chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
  };
  source.connect(analyser);
  source.connect(processor);
  processor.connect(mute);
  mute.connect(ctx.destination);

  let closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    processor.onaudioprocess = null;
    try {
      source.disconnect();
      processor.disconnect();
      mute.disconnect();
    } catch {
      /* déjà déconnecté */
    }
    stream.getTracks().forEach((t) => t.stop());
  };
  return {
    level: () => (closed ? 0 : analyserLevel(analyser, levelBuf)),
    seconds: () => (Date.now() - startedAt) / 1000,
    cancel: cleanup,
    stop: async () => {
      cleanup();
      const samples = downsample(chunks, ctx.sampleRate, 16000);
      return encodeWav(samples, 16000);
    },
  };
}
