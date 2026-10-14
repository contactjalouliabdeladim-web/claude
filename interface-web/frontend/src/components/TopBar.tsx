import { useAtlas } from "../controller";
import { GearIcon, PulseIcon } from "./Icons";

function Pill({ tone, label, title }: { tone: "ok" | "warn" | "bad" | "busy"; label: string; title: string }) {
  return (
    <span className={`pill pill-${tone}`} title={title}>
      <span className="pill-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export default function TopBar({ onActivity, onSettings, onBusiness, business }: { onActivity: () => void; onSettings: () => void; onBusiness: () => void; business: boolean }) {
  const a = useAtlas();
  const server =
    a.wsStatus === "open"
      ? { tone: "ok" as const, label: "Serveur connecté", title: "Connexion temps réel avec le serveur Atlas active." }
      : a.wsStatus === "connecting"
        ? { tone: "warn" as const, label: "Connexion…", title: "Tentative de connexion au serveur Atlas." }
        : { tone: "bad" as const, label: "Hors ligne", title: "Le serveur Atlas ne répond plus. Le PC Windows est-il allumé ?" };
  let hermes: { tone: "ok" | "warn" | "bad" | "busy"; label: string; title: string };
  if (!a.hermes) hermes = { tone: "warn", label: "Hermes ?", title: "État inconnu" };
  else if (a.hermes.mode === "demo") hermes = { tone: "warn", label: "Démo", title: "Mode démonstration : Hermes n'est pas utilisé." };
  else if (!a.hermes.authenticated)
    hermes = { tone: "bad", label: "Hermes indisponible", title: a.hermes.error || "Hermes ne répond pas." };
  else if (a.serverState.state === "waiting_approval")
    hermes = { tone: "warn", label: "Attention nécessaire", title: "Hermes attend ton accord pour une action." };
  else if (a.busy) hermes = { tone: "busy", label: "Action en cours", title: a.serverState.label };
  else hermes = { tone: "ok", label: "Hermes prêt", title: `Serveur API d'Hermes joignable (mode ${a.hermes.mode}). Le modèle n'est sollicité qu'à l'envoi d'un message.` };

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">Atlas</span>
      </div>
      <div className="topbar-status" role="status" aria-live="polite">
        <Pill {...server} />
        {a.wsStatus === "open" && <Pill {...hermes} />}
        {a.call.active && <Pill tone="busy" label="En appel" title="Appel vocal en cours avec Atlas" />}
      </div>
      <div className="topbar-actions">
        <button className={`btn btn-sm topbar-business ${business ? "active" : ""}`} onClick={onBusiness} aria-pressed={business} title="Ouvrir ou fermer la vue business">
          {business ? "Retour à Atlas" : "Vue business"}
        </button>
        <button className="icon-btn" onClick={onActivity} aria-label="Ouvrir le panneau d'activité" title="Activité">
          <PulseIcon />
        </button>
        <button className="icon-btn" onClick={onSettings} aria-label="Ouvrir les réglages" title="Réglages">
          <GearIcon />
        </button>
      </div>
    </header>
  );
}
