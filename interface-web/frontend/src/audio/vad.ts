/**
 * Micro « mains libres » : le micro reste ouvert, la parole est détectée automatiquement
 * (début quand le niveau dépasse le bruit de fond, fin après un court silence).
 * Chaque phrase détectée est livrée en WAV 16 kHz. Aucun audio ne quitte le navigateur
 * en dehors de ces phrases, envoyées au serveur Atlas pour transcription.
 *
 * Modes :
 *  - "listening" : détecte et capture la parole ;
 *  - "bargein"   : Atlas parle ; seule une voix nettement plus forte (toi) déclenche une
 *                  interruption — protège contre l'écho de la voix d'Atlas ;
 *  - "paused"    : ignore tout (transcription ou réflexion en cours, micro coupé).
 */
import { getAudioContext } from "./context";
import { downsample, encodeWav, micSupport, RecorderError } from "./recorder";

export type VadMode = "listening" | "bargein" | "paused";

export interface VadOptions {
  deviceId?: string;
  silenceMs?: number;
  maxSeconds?: number;
  onSpeechStart?: () => void;
  onBargeIn?: () => void;
  onUtterance: (wav: Blob, seconds: number) => void;
}

const FRAME = 2048;

function rms(data: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  return Math.sqrt(sum / data.length);
}

export class HandsFreeMic {
  private stream!: MediaStream;
  private source!: MediaStreamAudioSourceNode;
  private processor!: ScriptProcessorNode;
  private mute!: GainNode;
  private rate = 48000;
  private mode: VadMode = "listening";
  private floor = 0.008;
  private smoothLevel = 0;
  private loud = 0;
  private silence = 0;
  private speechFrames = 0;
  private inSpeech = false;
  private preRoll: Float32Array[] = [];
  private chunks: Float32Array[] = [];
  private closed = false;

  private constructor(private opts: VadOptions) {}

  static async open(opts: VadOptions): Promise<HandsFreeMic> {
    const support = micSupport();
    if (!support.ok) throw new RecorderError(support.kind!, support.message!);
    const mic = new HandsFreeMic(opts);
    await mic.start();
    return mic;
  }

  private async start() {
    const ctx = getAudioContext()!;
    if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
    const audio: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (this.opts.deviceId) audio.deviceId = { exact: this.opts.deviceId };
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (e: any) {
      const name = e && e.name;
      if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError")
        throw new RecorderError("denied", "Accès au micro refusé. Autorise-le dans les réglages du navigateur pour ce site.");
      if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError")
        throw new RecorderError("nodevice", "Aucun micro détecté (ou le micro choisi n'est plus branché).");
      if (name === "NotReadableError") throw new RecorderError("busy", "Le micro est utilisé par une autre application.");
      throw new RecorderError("unknown", "Impossible d'ouvrir le micro.");
    }
    this.rate = ctx.sampleRate;
    this.source = ctx.createMediaStreamSource(this.stream);
    this.processor = ctx.createScriptProcessor(FRAME, 1, 1);
    this.mute = ctx.createGain();
    this.mute.gain.value = 0;
    this.processor.onaudioprocess = (ev) => this.onFrame(new Float32Array(ev.inputBuffer.getChannelData(0)));
    this.source.connect(this.processor);
    this.processor.connect(this.mute);
    this.mute.connect(ctx.destination);
  }

  private get frameMs() {
    return (FRAME / this.rate) * 1000;
  }

  private onFrame(frame: Float32Array) {
    if (this.closed) return;
    const level = rms(frame);
    this.smoothLevel += (Math.min(1, level * 6) - this.smoothLevel) * 0.5;
    if (this.mode === "paused") {
      this.reset();
      return;
    }
    if (!this.inSpeech) {
      // Bruit de fond : descend vite, monte lentement.
      this.floor = level < this.floor ? this.floor * 0.85 + level * 0.15 : this.floor * 0.995 + level * 0.005;
      this.preRoll.push(frame);
      if (this.preRoll.length > 6) this.preRoll.shift();
      const bargeIn = this.mode === "bargein";
      const threshold = bargeIn ? Math.max(0.06, this.floor * 6) : Math.max(0.018, this.floor * 3.2);
      this.loud = level > threshold ? this.loud + 1 : 0;
      if (this.loud >= (bargeIn ? 6 : 3)) {
        this.inSpeech = true;
        this.chunks = this.preRoll.slice();
        this.preRoll = [];
        this.speechFrames = this.loud;
        this.silence = 0;
        this.loud = 0;
        if (bargeIn) {
          this.mode = "listening";
          if (this.opts.onBargeIn) this.opts.onBargeIn();
        }
        if (this.opts.onSpeechStart) this.opts.onSpeechStart();
      }
      return;
    }
    this.chunks.push(frame);
    if (level < Math.max(0.011, this.floor * 2)) this.silence++;
    else {
      this.silence = 0;
      this.speechFrames++;
    }
    const silenceFrames = Math.ceil((this.opts.silenceMs || 900) / this.frameMs);
    const maxFrames = Math.ceil(((this.opts.maxSeconds || 60) * 1000) / this.frameMs);
    if (this.silence >= silenceFrames || this.chunks.length >= maxFrames) this.finish();
  }

  private finish() {
    const keep = this.chunks.length - Math.max(0, this.silence - Math.ceil(300 / this.frameMs));
    const chunks = this.chunks.slice(0, keep);
    const speechMs = this.speechFrames * this.frameMs;
    this.reset();
    if (speechMs < 350) return; // bruit bref (clic, toux) : ignoré
    const samples = downsample(chunks, this.rate, 16000);
    this.opts.onUtterance(encodeWav(samples, 16000), samples.length / 16000);
  }

  private reset() {
    this.inSpeech = false;
    this.chunks = [];
    this.loud = 0;
    this.silence = 0;
    this.speechFrames = 0;
  }

  setMode(mode: VadMode) {
    if (mode !== this.mode) {
      if (mode !== "listening" || this.mode === "paused") this.reset();
      this.mode = mode;
    }
  }

  /** Termine tout de suite la phrase en cours (bouton « J'ai fini »). Vrai si une phrase était en cours. */
  flush(): boolean {
    if (!this.inSpeech) return false;
    this.silence = 0;
    this.finish();
    return true;
  }

  getMode(): VadMode {
    return this.mode;
  }

  /** Vrai pendant qu'une phrase est en cours de capture. */
  get capturing(): boolean {
    return this.inSpeech;
  }

  level(): number {
    return this.closed ? 0 : this.smoothLevel;
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    try {
      this.processor.onaudioprocess = null;
      this.source.disconnect();
      this.processor.disconnect();
      this.mute.disconnect();
    } catch {
      /* déjà déconnecté */
    }
    this.stream.getTracks().forEach((t) => t.stop());
  }
}
