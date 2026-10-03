/**
 * Lecture vocale « au fil de l'eau » : la réponse d'Atlas est découpée en phrases pendant
 * qu'elle s'écrit ; chaque phrase est synthétisée puis jouée, la suivante étant préparée
 * pendant la lecture. La voix commence donc dès la première phrase.
 */
import { PlaybackBlocked, VoicePlayer } from "./player";

export interface SpeechCallbacks {
  fetchAudio: (messageId: string, text: string) => Promise<{ data: ArrayBuffer; mime: string }>;
  volume: () => number;
  onStart: (messageId: string) => void;
  onIdle: (messageId: string, how: "finished" | "stopped" | "error") => void;
  onError: (message: string) => void;
  onBlocked: (messageId: string) => void;
}

const BOUNDARY = /([.!?…]+["»”)]*\s+|\n+|[:;]\s+)/g;

function fences(text: string): number {
  const m = text.match(/```/g);
  return m ? m.length : 0;
}

export class SpeechQueue {
  messageId: string | null = null;
  private text = "";
  private cursor = 0;
  private done = false;
  private queue: string[] = [];
  private playing = false;
  private stopped = true;
  private started = false;
  private pending = new Map<string, Promise<{ data: ArrayBuffer; mime: string }>>();

  constructor(private player: VoicePlayer, private cb: SpeechCallbacks) {}

  get active(): boolean {
    return !this.stopped;
  }

  begin(messageId: string, initialText = "") {
    this.stop(false);
    this.messageId = messageId;
    this.text = initialText;
    this.cursor = 0;
    this.done = false;
    this.queue = [];
    this.pending.clear();
    this.stopped = false;
    this.started = false;
    this.extract(false);
  }

  feed(delta: string) {
    if (this.stopped) return;
    this.text += delta;
    this.extract(false);
  }

  /** Fin de la réponse : `finalText` remplace le texte accumulé s'il est fourni. */
  finish(finalText?: string) {
    if (this.stopped) return;
    if (finalText !== undefined && finalText.length >= this.cursor && finalText.indexOf(this.text.slice(0, this.cursor).trim()) >= 0) {
      // On garde ce qui a déjà été lu ; on complète avec la fin définitive.
      const spoken = this.text.slice(0, this.cursor).trim();
      const pos = spoken ? finalText.indexOf(spoken) + spoken.length : 0;
      this.text = finalText;
      this.cursor = pos;
    }
    this.extract(true);
    this.done = true;
    this.pump();
  }

  stop(notify = true) {
    const wasActive = !this.stopped;
    this.stopped = true;
    this.queue = [];
    this.pending.clear();
    if (this.playing) this.player.stop();
    this.playing = false;
    if (wasActive && notify && this.messageId) this.cb.onIdle(this.messageId, "stopped");
  }

  private extract(force: boolean) {
    for (;;) {
      const rest = this.text.slice(this.cursor);
      if (!rest.trim()) {
        this.cursor = this.text.length;
        break;
      }
      const minLen = this.started || this.queue.length ? 60 : 24;
      let end = -1;
      BOUNDARY.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = BOUNDARY.exec(rest))) {
        const e = m.index + m[0].length;
        if (rest.slice(0, e).trim().length >= minLen && fences(this.text.slice(0, this.cursor + e)) % 2 === 0) {
          end = e;
          break;
        }
      }
      if (end < 0 && rest.length > 420 && fences(this.text.slice(0, this.text.length)) % 2 === 0) {
        const space = rest.lastIndexOf(" ", 320);
        end = space > 80 ? space + 1 : 320;
      }
      if (end < 0) {
        if (!force) break;
        end = rest.length;
      }
      const segment = rest.slice(0, end).trim();
      this.cursor += end;
      if (/[A-Za-z0-9À-ÿ]/.test(segment)) this.queue.push(segment);
    }
    this.pump();
  }

  private fetch(segment: string) {
    let p = this.pending.get(segment);
    if (!p) {
      p = this.cb.fetchAudio(this.messageId!, segment);
      p.catch(() => undefined);
      this.pending.set(segment, p);
    }
    return p;
  }

  private async pump() {
    if (this.playing || this.stopped) return;
    const segment = this.queue.shift();
    if (segment === undefined) {
      if (this.done) {
        this.stopped = true;
        if (this.messageId) this.cb.onIdle(this.messageId, "finished");
      }
      return;
    }
    this.playing = true;
    const messageId = this.messageId!;
    try {
      const audio = await this.fetch(segment);
      this.pending.delete(segment);
      if (this.stopped || messageId !== this.messageId) return;
      if (this.queue.length) this.fetch(this.queue[0]); // prépare la phrase suivante
      if (!this.started) {
        this.started = true;
        this.cb.onStart(messageId);
      }
      await this.player.play(audio.data, audio.mime, this.cb.volume());
    } catch (e) {
      if (this.stopped || messageId !== this.messageId) return;
      this.playing = false;
      this.stop(false);
      if (e instanceof PlaybackBlocked) this.cb.onBlocked(messageId);
      else this.cb.onError((e as Error).message || "Voix indisponible.");
      this.cb.onIdle(messageId, "error");
      return;
    } finally {
      if (messageId === this.messageId) this.playing = false;
    }
    if (!this.stopped && messageId === this.messageId) this.pump();
  }
}
