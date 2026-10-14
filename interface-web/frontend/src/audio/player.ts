/**
 * Lecture de la voix d'Atlas sur CET appareil, avec analyse réelle du signal joué
 * (l'orbe suit l'amplitude). Si l'analyse est impossible, lecture simple et l'orbe
 * affiche un état « lecture » sans prétendre suivre la voix.
 */
import { analyserLevel, getAudioContext } from "./context";

export class PlaybackBlocked extends Error {}

export class VoicePlayer {
  private source: AudioBufferSourceNode | null = null;
  private element: HTMLAudioElement | null = null;
  private analyser: AnalyserNode | null = null;
  private gain: GainNode | null = null;
  private buf = new Uint8Array(1024);
  private finish: (() => void) | null = null;
  analysed = false;
  playing = false;

  level(): number {
    if (!this.playing || !this.analyser) return 0;
    return analyserLevel(this.analyser, this.buf);
  }

  setVolume(v: number) {
    if (this.gain) this.gain.gain.value = v;
    if (this.element) this.element.volume = v;
  }

  async play(data: ArrayBuffer, mime: string, volume: number): Promise<"ended" | "stopped"> {
    this.stop();
    const ctx = getAudioContext();
    if (ctx) {
      if (ctx.state === "suspended") {
        await ctx.resume().catch(() => undefined);
        if ((ctx.state as string) === "suspended") throw new PlaybackBlocked("Lecture bloquée");
      }
      let decoded: AudioBuffer | null = null;
      try {
        decoded = await new Promise<AudioBuffer>((resolve, reject) => {
          const p = ctx.decodeAudioData(data.slice(0), resolve, reject);
          if (p && typeof (p as any).catch === "function") (p as Promise<AudioBuffer>).catch(reject);
        });
      } catch {
        decoded = null;
      }
      if (decoded) {
        const src = ctx.createBufferSource();
        src.buffer = decoded;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        const gain = ctx.createGain();
        gain.gain.value = volume;
        src.connect(analyser);
        analyser.connect(gain);
        gain.connect(ctx.destination);
        this.source = src;
        this.analyser = analyser;
        this.gain = gain;
        this.analysed = true;
        this.playing = true;
        return new Promise((resolve) => {
          let done = false;
          const end = (how: "ended" | "stopped") => {
            if (done) return;
            done = true;
            this.playing = false;
            this.finish = null;
            resolve(how);
          };
          this.finish = () => end("stopped");
          src.onended = () => end("ended");
          src.start(0);
        });
      }
    }
    // Repli : élément <audio> sans analyse du signal.
    const url = URL.createObjectURL(new Blob([data], { type: mime }));
    const el = new Audio(url);
    el.volume = volume;
    this.element = el;
    this.analysed = false;
    this.playing = true;
    return new Promise((resolve, reject) => {
      let done = false;
      const end = (how: "ended" | "stopped") => {
        if (done) return;
        done = true;
        this.playing = false;
        this.finish = null;
        URL.revokeObjectURL(url);
        resolve(how);
      };
      this.finish = () => end("stopped");
      el.onended = () => end("ended");
      el.play().catch(() => {
        this.playing = false;
        URL.revokeObjectURL(url);
        reject(new PlaybackBlocked("Lecture bloquée"));
      });
    });
  }

  stop() {
    if (this.source) {
      try {
        this.source.onended = null;
        this.source.stop();
        this.source.disconnect();
      } catch {
        /* déjà arrêtée */
      }
      this.source = null;
    }
    if (this.element) {
      this.element.pause();
      this.element = null;
    }
    this.analyser = null;
    this.gain = null;
    this.playing = false;
    if (this.finish) this.finish();
  }
}
