import { useEffect, useState } from "react";
import { api } from "../api";
import { useAtlas } from "../controller";
import { HangUpIcon, MicIcon, MicOffIcon, MuteIcon, PhoneIcon, SparkIcon, StopIcon, VolumeIcon } from "./Icons";
import Orb from "./Orb";

const PHASE_HINT: Record<string, string> = {
  connecting: "Ouverture du micro…",
  listening: "Parle naturellement : je détecte quand tu as fini.",
  hearing: "Je t'écoute…",
  transcribing: "Je transcris ce que tu as dit…",
  waiting: "",
  speaking: "Tu peux me couper la parole en parlant.",
  muted: "Micro coupé : réactive-le pour me parler.",
};

function useElapsed(since: number) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!since) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [since]);
  const s = Math.max(0, Math.floor((now - since) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function CallView() {
  const a = useAtlas();
  const c = a.call;
  const elapsed = useElapsed(c.startedAt);
  const hint = c.phase === "speaking" && !a.bargeIn ? "" : PHASE_HINT[c.phase];
  const working = a.busy && (c.phase === "waiting" || c.phase === "speaking");
  return (
    <div className="call-view" role="region" aria-label="Appel en cours avec Atlas">
      <div className="call-top">
        <span className="call-live" aria-hidden="true" />
        <span>En appel avec Atlas</span>
        <span className="call-timer">{elapsed}</span>
      </div>
      <div className={`orb-wrap call orb-${a.orbState}`}>
        <Orb state={a.orbState} level={a.audioLevel} reduceMotion={a.reduceMotion} />
      </div>
      <div className="orb-status" role="status" aria-live="polite">
        <div className="orb-label">{a.orbLabel}</div>
        {hint && <div className="orb-sub">{hint}</div>}
      </div>
      <div className="captions" aria-live="polite">
        {c.userCaption && (
          <p className="cap cap-user">
            <span>Toi</span>
            {c.userCaption}
          </p>
        )}
        {c.atlasCaption && (
          <p className="cap cap-atlas">
            <span>Atlas</span>
            {c.atlasCaption.length > 280 ? "…" + c.atlasCaption.slice(-280) : c.atlasCaption}
          </p>
        )}
      </div>
      <div className="call-controls">
        <button className={`round-btn ${c.muted ? "on" : ""}`} onClick={a.toggleMute} aria-pressed={c.muted} aria-label={c.muted ? "Réactiver le micro" : "Couper le micro"}>
          {c.muted ? <MicOffIcon size={22} /> : <MicIcon size={22} />}
          <span>{c.muted ? "Micro coupé" : "Micro"}</span>
        </button>
        <button className="round-btn hangup" onClick={a.endCall} aria-label="Raccrocher">
          <HangUpIcon size={26} />
          <span>Raccrocher</span>
        </button>
        {a.localVoice === "speaking" ? (
          <button className="round-btn" onClick={a.stopSpeaking} aria-label="Couper la voix d'Atlas (la tâche continue)">
            <MuteIcon size={22} />
            <span>Couper la voix</span>
          </button>
        ) : working && a.serverState.can_stop !== false ? (
          <button className="round-btn" onClick={a.stopTask} aria-label="Arrêter la tâche en cours">
            <StopIcon size={22} />
            <span>Arrêter</span>
          </button>
        ) : (
          <span className="round-btn ghost" aria-hidden="true" />
        )}
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  "Qu'est-ce qu'il me reste à faire aujourd'hui ?",
  "Fais le bilan de ma journée avec moi.",
  "Aide-moi à choisir mes 3 priorités.",
];

export default function CenterStage() {
  const a = useAtlas();
  const [daily, setDaily] = useState<{ available: boolean; prompt?: string } | null>(null);
  useEffect(() => {
    if (!a.hermes || !a.hermes.authenticated) return;
    api<typeof daily>("/api/skills/daily").then(setDaily).catch(() => setDaily(null));
  }, [a.hermes && a.hermes.authenticated]);

  if (a.call.active) {
    return (
      <section className="center-stage in-call" aria-label="Appel">
        <CallView />
      </section>
    );
  }

  const listening = a.localVoice === "listening";
  const transcribing = a.localVoice === "transcribing";
  const sttOk = !a.boot || a.boot.voice.stt.available;
  const blocked = a.offline || a.busy || !sttOk;
  const sub =
    a.orbState === "error" && a.hermes
      ? a.hermes.error
      : a.orbState === "offline"
        ? "Le serveur Atlas ne répond pas. Vérifie que le PC Windows est allumé et qu'Atlas est lancé."
        : listening
          ? "Parle, j'enverrai dès que tu marques une pause."
          : null;

  return (
    <section className="center-stage" aria-label="Atlas">
      <div className={`orb-wrap orb-${a.orbState}`}>
        <Orb state={a.orbState} level={a.audioLevel} reduceMotion={a.reduceMotion} />
      </div>
      <div className="orb-status" role="status" aria-live="polite">
        <div className="orb-label">{a.orbLabel}</div>
        {sub && <div className={`orb-sub ${a.orbState === "error" || a.orbState === "offline" ? "warn" : ""}`}>{sub}</div>}
        {a.orbState === "offline" && <button className="btn btn-ghost btn-sm" onClick={a.reconnect}>Reconnecter</button>}
      </div>

      <div className="voice-controls">
        <button
          className="call-btn"
          onClick={a.startCall}
          disabled={blocked || listening || transcribing}
          title={!sttOk ? "Transcription non disponible (Réglages > Voix)" : a.busy ? "Atlas est occupé" : "Conversation vocale en direct"}
        >
          <PhoneIcon size={22} />
          <span>Appeler Atlas</span>
        </button>
        {listening ? (
          <button className="btn btn-outline" onClick={a.cancelListening} aria-label="Annuler la dictée">
            <StopIcon size={16} /> Annuler
          </button>
        ) : (
          <button className="btn btn-outline" onClick={a.startListening} disabled={blocked || transcribing} aria-label="Dicter un seul message">
            <MicIcon size={16} /> {transcribing ? "Transcription…" : "Dicter"}
          </button>
        )}
        {a.localVoice === "speaking" && (
          <button className="btn btn-outline" onClick={a.stopSpeaking} aria-label="Couper la voix (la tâche n'est pas annulée)">
            <MuteIcon size={16} /> Couper la voix
          </button>
        )}
      </div>

      <div className="voice-options">
        <button className={`toggle ${a.autoplay ? "on" : ""}`} role="switch" aria-checked={a.autoplay} onClick={() => a.setAutoplay(!a.autoplay)}
          title="Lire à voix haute les réponses à tes dictées, sur cet appareil">
          {a.autoplay ? <VolumeIcon size={16} /> : <MuteIcon size={16} />}
          <span>Réponse vocale {a.autoplay ? "activée" : "désactivée"}</span>
        </button>
        <label className="volume">
          <span className="sr-only">Volume de la voix</span>
          <input type="range" min={0} max={1} step={0.05} value={a.volume} onChange={(e) => a.setVolume(parseFloat(e.target.value))} aria-label="Volume de la voix" />
        </label>
      </div>

      <div className="suggestions" aria-label="Suggestions">
        {daily && daily.available && daily.prompt && (
          <button className="suggestion primary" disabled={a.busy || a.sending || a.offline} onClick={() => a.send(daily.prompt!)}>
            <SparkIcon size={14} /> Préparer ma journée
          </button>
        )}
        {SUGGESTIONS.map((s) => (
          <button key={s} className="suggestion" disabled={a.busy || a.sending || a.offline} onClick={() => a.send(s)}>
            {s}
          </button>
        ))}
      </div>

      {a.blockedPlayback && (
        <div className="notice">
          Le navigateur a bloqué la lecture automatique.
          <button className="btn btn-gold btn-sm" onClick={() => a.speak(a.blockedPlayback!)}>▶ Écouter</button>
        </div>
      )}
    </section>
  );
}
