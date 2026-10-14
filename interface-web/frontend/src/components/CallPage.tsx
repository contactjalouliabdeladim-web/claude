import { useEffect, useRef, useState } from "react";
import { getAudioContext, unlockAudio } from "../audio/context";
import { useAtlas, type CallSlot } from "../controller";
import { CockpitGauge } from "./Cockpit";
import { HangUpIcon, MicIcon, MicOffIcon, MuteIcon, PhoneIcon } from "./Icons";
import Orb from "./Orb";

const SLOTS: CallSlot[] = ["morning", "afternoon", "evening", "test", "live"];

const PHASE_HINT: Record<string, string> = {
  connecting: "Ouverture du micro…",
  listening: "Parle naturellement. Dis « c'est bon » ou « à plus » pour raccrocher.",
  hearing: "Je t'écoute…",
  transcribing: "Je transcris…",
  waiting: "",
  speaking: "Tu peux me couper la parole en parlant.",
  muted: "Micro coupé.",
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

/** Page « Appel » (téléphone, appli installée) : décroche toute seule, conversation mains libres. */
export default function CallPage() {
  const a = useAtlas();
  const [slot] = useState<CallSlot>(() => {
    const v = new URLSearchParams(window.location.search).get("slot") as CallSlot | null;
    return v && SLOTS.includes(v) ? v : "live";
  });
  const [needsTap, setNeedsTap] = useState(false);
  const [ended, setEnded] = useState(false);
  const started = useRef(false);
  const wasActive = useRef(false);
  const elapsed = useElapsed(a.call.startedAt);

  const pickUp = (s: CallSlot) => {
    unlockAudio();
    setNeedsTap(false);
    setEnded(false);
    a.startCall(s);
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // Un rechargement de la page ne doit pas relancer le message d'ouverture.
    window.history.replaceState(null, "", "/appel");
    unlockAudio();
    const t = window.setTimeout(() => {
      const ctx = getAudioContext();
      // Sans geste, le navigateur peut bloquer le son : on demande alors un seul toucher.
      if (!ctx || ctx.state === "running") a.startCall(slot);
      else setNeedsTap(true);
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (a.call.active) wasActive.current = true;
    else if (wasActive.current) setEnded(true);
  }, [a.call.active]);

  const c = a.call;
  const hint = c.phase === "speaking" && !a.bargeIn ? "" : PHASE_HINT[c.phase];

  return (
    <main className={`call-page orb-${a.orbState}`} aria-label="Appel avec Atlas">
      <header className="call-page-top">
        {c.active ? (
          <>
            <span className="call-live" aria-hidden="true" />
            <span>En appel avec Atlas</span>
            <span className="call-timer">{elapsed}</span>
          </>
        ) : (
          <span>{ended ? "Appel terminé" : needsTap ? "Atlas t'appelle" : "Connexion…"}</span>
        )}
      </header>

      <div className="call-page-orb">
        <Orb state={a.orbState} level={a.audioLevel} reduceMotion={a.reduceMotion} />
      </div>

      <div className="orb-status" role="status" aria-live="polite">
        <div className="orb-label">{c.active ? a.orbLabel : ended ? "À plus tard" : "Atlas"}</div>
        {c.active && hint && <div className="orb-sub">{hint}</div>}
      </div>

      {c.active && (
        <div className="captions" aria-live="polite">
          {c.userCaption && <p className="cap cap-user"><span>Toi</span>{c.userCaption}</p>}
          {c.atlasCaption && (
            <p className="cap cap-atlas"><span>Atlas</span>{c.atlasCaption.length > 220 ? "…" + c.atlasCaption.slice(-220) : c.atlasCaption}</p>
          )}
        </div>
      )}

      <CockpitGauge compact />

      <div className="call-page-actions">
        {c.active ? (
          <>
            <button className={`round-btn ${c.muted ? "on" : ""}`} onClick={a.toggleMute} aria-pressed={c.muted} aria-label={c.muted ? "Réactiver le micro" : "Couper le micro"}>
              {c.muted ? <MicOffIcon size={24} /> : <MicIcon size={24} />}
              <span>{c.muted ? "Micro coupé" : "Micro"}</span>
            </button>
            <button className="round-btn hangup big" onClick={a.endCall} aria-label="Raccrocher">
              <HangUpIcon size={34} />
              <span>Raccrocher</span>
            </button>
            {a.localVoice === "speaking" ? (
              <button className="round-btn" onClick={a.stopSpeaking} aria-label="Couper la voix d'Atlas">
                <MuteIcon size={24} />
                <span>Couper la voix</span>
              </button>
            ) : (
              <span className="round-btn ghost" aria-hidden="true" />
            )}
          </>
        ) : needsTap ? (
          <button className="round-btn pickup big" onClick={() => pickUp(slot)} aria-label="Décrocher">
            <PhoneIcon size={34} />
            <span>Décrocher</span>
          </button>
        ) : ended ? (
          <>
            <button className="round-btn pickup big" onClick={() => pickUp("live")} aria-label="Rappeler Atlas">
              <PhoneIcon size={30} />
              <span>Rappeler</span>
            </button>
            <a className="btn btn-outline" href="/">Ouvrir le tableau de bord</a>
          </>
        ) : null}
      </div>

      {a.blockedPlayback && (
        <div className="notice">
          Le navigateur a bloqué le son.
          <button className="btn btn-gold btn-sm" onClick={() => a.speak(a.blockedPlayback!)}>▶ Écouter</button>
        </div>
      )}
    </main>
  );
}
