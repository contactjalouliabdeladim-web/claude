import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { listInputDevices, micSupport } from "../audio/recorder";
import { useAtlas } from "../controller";
import { fmt } from "../time";
import { CloseIcon } from "./Icons";

const ZONES = [
  "Africa/Casablanca", "Europe/Paris", "Europe/London", "Europe/Brussels", "Europe/Madrid", "Africa/Algiers",
  "Africa/Tunis", "America/Montreal", "America/New_York", "Asia/Dubai", "UTC",
];

type Tab = "daily" | "voice" | "connection" | "data";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="set-row">
      <div className="set-label">{label}</div>
      <div className="set-value">{children}</div>
    </div>
  );
}

function yes(v: boolean) {
  return v ? "oui" : "non";
}

export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  const a = useAtlas();
  const [tab, setTab] = useState<Tab>("daily");
  const [tz, setTz] = useState(a.prefs ? a.prefs.timezone : "Africa/Casablanca");
  const [mics, setMics] = useState<{ id: string; label: string }[]>([]);
  const [devices, setDevices] = useState<{ device_label: string; last_seen: number }[]>([]);
  const closeRef = useRef<HTMLButtonElement>(null);
  const boot = a.boot;

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    listInputDevices().then(setMics).catch(() => setMics([]));
    api<{ device_label: string; last_seen: number }[]>("/api/devices").then(setDevices).catch(() => undefined);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveTz = async () => {
    try {
      await a.updatePrefs({ timezone: tz.trim() });
      await a.loadPriorities();
      a.toast({ kind: "success", text: "Fuseau horaire enregistré." });
    } catch (e) {
      a.toast({ kind: "error", text: (e as Error).message });
    }
  };

  const mic = micSupport();
  const h = a.hermes;
  const runtime = (() => {
    for (let i = a.messages.length - 1; i >= 0; i--) {
      const r = a.messages[i].meta.runtime;
      if (r && r.model) return r;
    }
    return boot && boot.last_runtime && boot.last_runtime.model ? boot.last_runtime : null;
  })();
  const usage = (() => {
    for (let i = a.messages.length - 1; i >= 0; i--) {
      const u = a.messages[i].meta.usage;
      if (u && (u.total_tokens || u.input_tokens)) return u;
    }
    return null;
  })();

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Réglages" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Réglages</h2>
          <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label="Fermer les réglages"><CloseIcon /></button>
        </div>
        <div className="tabs" role="tablist">
          {([
            ["daily", "Quotidien"],
            ["voice", "Voix"],
            ["connection", "Connexion"],
            ["data", "Données"],
          ] as [Tab, string][]).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
        <div className="modal-body">
          {tab === "daily" && a.prefs && (
            <>
              <Row label="Langue">Français</Row>
              <Row label="Fuseau horaire">
                <div className="inline-form">
                  <input list="tz-list" value={tz} onChange={(e) => setTz(e.target.value)} aria-label="Fuseau horaire IANA" />
                  <datalist id="tz-list">{ZONES.map((z) => <option key={z} value={z} />)}</datalist>
                  <button className="btn btn-gold btn-sm" onClick={saveTz} disabled={tz === a.prefs.timezone}>Enregistrer</button>
                </div>
                <p className="muted small">Format IANA (ex. Africa/Casablanca). L'heure et les priorités du jour en dépendent.</p>
              </Row>
              <Row label="Animations">
                <select
                  value={a.prefs.reduce_motion}
                  onChange={(e) => a.updatePrefs({ reduce_motion: e.target.value as any })}
                  aria-label="Réduction des animations"
                >
                  <option value="system">Suivre le réglage du système</option>
                  <option value="on">Réduites</option>
                  <option value="off">Normales</option>
                </select>
              </Row>
            </>
          )}

          {tab === "voice" && a.prefs && boot && (
            <>
              <Row label="Réponse vocale">
                <label className="check">
                  <input type="checkbox" checked={a.autoplay} onChange={(e) => a.setAutoplay(e.target.checked)} />
                  Lire automatiquement les réponses à mes demandes vocales (sur cet appareil)
                </label>
              </Row>
              <Row label="Volume">
                <input type="range" min={0} max={1} step={0.05} value={a.volume} onChange={(e) => a.setVolume(parseFloat(e.target.value))} aria-label="Volume" />
              </Row>
              <Row label="Micro">
                {mics.length ? (
                  <select value={a.micDevice} onChange={(e) => a.setMicDevice(e.target.value)} aria-label="Choix du micro">
                    <option value="">Micro par défaut</option>
                    {mics.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                ) : (
                  <span className="muted">Liste indisponible (les noms apparaissent après une première autorisation du micro).</span>
                )}
              </Row>
              <Row label="Appel en direct">
                <label className="check">
                  <input type="checkbox" checked={a.bargeIn} onChange={(e) => a.setBargeIn(e.target.checked)} />
                  Pouvoir couper la parole à Atlas en parlant (un casque ou des écouteurs évitent l'écho)
                </label>
              </Row>
              <Row label="Dictée">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={a.prefs.voice_confirm_before_send}
                    onChange={(e) => a.updatePrefs({ voice_confirm_before_send: e.target.checked })}
                  />
                  Vérifier le texte reconnu avant de l'envoyer
                </label>
              </Row>
              <Row label="Micro sur cet appareil">{mic.ok ? "Disponible" : mic.message}</Row>
              <Row label="Transcription">
                {boot.voice.stt.where} {boot.voice.stt.available ? "" : "— indisponible"}
                {boot.voice.stt.hint && <p className="muted small">{boot.voice.stt.hint}</p>}
              </Row>
              <Row label="Synthèse">
                {boot.voice.tts.where}
                {boot.voice.tts.voice && ` — voix ${boot.voice.tts.voice}`}
                {boot.voice.tts.provider === "edge" && (
                  <p className="muted small">Service distant de Microsoft : le texte des réponses lues lui est envoyé. Gratuit, sans clé, sans garantie de disponibilité.</p>
                )}
              </Row>
              <Row label="Config. voix d'Hermes">
                TTS : {boot.voice.hermes_config.tts_provider || "non lu"} · voix Edge : {boot.voice.hermes_config.edge_voice || "non lue"} · STT : {boot.voice.hermes_config.stt_provider || "auto/non lu"}
              </Row>
            </>
          )}

          {tab === "connection" && boot && (
            <>
              <Row label="Serveur Atlas">{a.wsStatus === "open" ? "Connecté (temps réel)" : a.wsStatus === "connecting" ? "Connexion…" : "Déconnecté"}</Row>
              <Row label="Hermes joignable">{h ? yes(h.reachable) : "?"}</Row>
              <Row label="Clé acceptée">{h ? yes(h.authenticated) : "?"}</Row>
              <Row label="Intégration">
                {h ? ({ sessions: "API officielle — sessions + flux d'événements", chat_completions: "API officielle — mode compatibilité", unavailable: "indisponible", demo: "DÉMONSTRATION (réponses simulées)" } as any)[h.mode] : "?"}
              </Row>
              {h && h.error && <Row label="Problème"><span className="warn-text">{h.error}</span></Row>}
              <Row label="Modèle">
                {runtime
                  ? `${runtime.provider || "?"} / ${runtime.model} (rapporté par Hermes au dernier échange)`
                  : "Inconnu tant qu'aucun échange n'a eu lieu. Il se règle avec « hermes model », pas ici."}
              </Row>
              <Row label="Consommation">
                {usage ? `Dernière réponse : ${usage.total_tokens || (usage.input_tokens || 0) + (usage.output_tokens || 0)} jetons (rapporté par Hermes).` : "Indisponible."}{" "}
                <span className="muted small">Le quota restant de ton abonnement n'est pas connu d'Atlas.</span>
              </Row>
              <Row label="Outils Atlas dans Hermes">
                {boot.mcp && boot.mcp.last_seen
                  ? `Connectés (dernier contact d'Hermes : ${fmt(boot.mcp.last_seen * 1000, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}). Atlas peut ajouter et modifier tes tâches à ta demande.`
                  : "Pas encore connectés : lance scripts\\windows\\Connecter-Outils-Atlas.ps1 puis redémarre la passerelle Hermes."}
              </Row>
              <Row label="Accès réseau local">
                {boot.network.lan ? (
                  <>
                    Activé en {boot.network.https ? "HTTPS" : "HTTP"} :
                    <ul className="plain">{boot.network.urls.map((u) => <li key={u}><code>{u}</code></li>)}</ul>
                    {boot.network.ca_available && <a className="btn btn-ghost btn-sm" href="/atlas-ca.crt">Télécharger le certificat Atlas CA</a>}
                  </>
                ) : (
                  "Désactivé (accès uniquement depuis ce PC). Voir le guide « Accès depuis le Mac »."
                )}
              </Row>
              <Row label="Appareils connectés">
                <ul className="plain">{devices.map((d, i) => <li key={i}>{d.device_label} — vu le {fmt(d.last_seen * 1000, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</li>)}</ul>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => window.confirm("Déconnecter tous les autres appareils ?") && api("/api/devices/revoke-others", { method: "POST" }).then(() => a.toast({ kind: "success", text: "Autres appareils déconnectés." }))}
                >
                  Déconnecter les autres appareils
                </button>
              </Row>
              <Row label="Cet appareil">
                <button className="btn btn-outline btn-sm" onClick={a.logout}>Se déconnecter</button>
              </Row>
            </>
          )}

          {tab === "data" && boot && (
            <>
              <Row label="Export">
                <a className="btn btn-ghost btn-sm" href="/api/export?format=json">Notes + priorités (JSON)</a>{" "}
                <a className="btn btn-ghost btn-sm" href="/api/export?format=md">Lisible (Markdown)</a>
              </Row>
              <Row label="Stockage">
                <code className="wrap">{boot.data_dir}</code>
                <p className="muted small">Sur le PC Windows. Sauvegarde : script « Sauvegarder-Atlas ». La mémoire d'Hermes reste gérée par Hermes.</p>
              </Row>
              <Row label="Version">Atlas {boot.version} · schéma {boot.schema_version}{boot.demo ? " · MODE DÉMO" : ""}</Row>
              <Row label="Navigateur">
                <span className="small">
                  Contexte sécurisé : {yes(window.isSecureContext !== false)} · Micro : {yes(!!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia))} · Audio Web :{" "}
                  {yes(!!((window as any).AudioContext || (window as any).webkitAudioContext))}
                </span>
                <p className="muted small wrap">{navigator.userAgent}</p>
              </Row>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
