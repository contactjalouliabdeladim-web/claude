import { useAtlas } from "../controller";
import { MicIcon, MuteIcon, StopIcon, VolumeIcon } from "./Icons";
import Orb from "./Orb";

export default function CenterStage({ compact = false }: { compact?: boolean }) {
  const a = useAtlas();
  const listening = a.localVoice === "listening";
  const transcribing = a.localVoice === "transcribing";
  const speaking = a.localVoice === "speaking";
  const sttOk = !a.boot || a.boot.voice.stt.available;
  const micDisabled = transcribing || a.offline || (!listening && (a.busy || !sttOk));
  const sub =
    a.orbState === "speaking" && !a.offline
      ? null
      : a.orbState === "error" && a.hermes
        ? a.hermes.error
        : a.orbState === "offline"
          ? "Le serveur Atlas ne répond pas. Vérifie que le PC Windows est allumé et qu'Atlas est lancé."
          : null;

  return (
    <section className={`center-stage ${compact ? "compact" : ""}`} aria-label="Atlas">
      <div className={`orb-wrap orb-${a.orbState}`}>
        <Orb state={a.orbState} level={a.audioLevel} reduceMotion={a.reduceMotion} />
      </div>
      <div className="orb-status" role="status" aria-live="polite">
        <div className="orb-label">{a.orbLabel}</div>
        {listening && <div className="orb-sub">{a.recordSeconds}s · touche « Terminer » quand tu as fini</div>}
        {sub && <div className="orb-sub warn">{sub}</div>}
        {a.orbState === "offline" && (
          <button className="btn btn-ghost btn-sm" onClick={a.reconnect}>Reconnecter</button>
        )}
      </div>

      <div className="voice-controls">
        {listening ? (
          <>
            <button className="btn btn-ghost" onClick={a.cancelListening} aria-label="Annuler l'enregistrement">Annuler</button>
            <button className="mic-btn recording" onClick={a.finishListening} aria-label="Terminer l'enregistrement et envoyer">
              <StopIcon size={26} />
              <span className="mic-text">Terminer</span>
            </button>
          </>
        ) : (
          <button
            className="mic-btn"
            onClick={a.startListening}
            disabled={micDisabled}
            aria-label="Parler à Atlas (activer le micro)"
            title={!sttOk ? "Transcription non disponible (voir Réglages > Voix)" : a.busy ? "Atlas est occupé" : "Parler à Atlas"}
          >
            <MicIcon size={26} />
            <span className="mic-text">{transcribing ? "Transcription…" : "Parler"}</span>
          </button>
        )}
        {speaking && (
          <button className="btn btn-outline" onClick={a.stopSpeaking} aria-label="Couper la voix (la tâche n'est pas annulée)">
            <MuteIcon size={18} /> Couper la voix
          </button>
        )}
      </div>

      {!compact && (
        <div className="voice-options">
          <button
            className={`toggle ${a.autoplay ? "on" : ""}`}
            role="switch"
            aria-checked={a.autoplay}
            onClick={() => a.setAutoplay(!a.autoplay)}
            title="Lire à voix haute les réponses à tes demandes vocales, sur cet appareil"
          >
            {a.autoplay ? <VolumeIcon size={16} /> : <MuteIcon size={16} />}
            <span>Réponse vocale {a.autoplay ? "activée" : "désactivée"}</span>
          </button>
          <label className="volume">
            <span className="sr-only">Volume de la voix</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={a.volume}
              onChange={(e) => a.setVolume(parseFloat(e.target.value))}
              aria-label="Volume de la voix"
            />
          </label>
        </div>
      )}
      {a.blockedPlayback && (
        <div className="notice">
          Le navigateur a bloqué la lecture automatique.
          <button className="btn btn-gold btn-sm" onClick={() => a.speak(a.blockedPlayback!)}>▶ Écouter</button>
        </div>
      )}
    </section>
  );
}
