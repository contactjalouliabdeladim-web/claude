import { useEffect, useRef, useState } from "react";
import { useAtlas } from "../controller";
import { fmt } from "../time";
import { CloseIcon } from "./Icons";

const STATUS_TEXT: Record<string, string> = {
  ok: "réussi",
  running: "en cours",
  error: "échec",
  warning: "attention",
  waiting: "en attente",
  info: "info",
};

const CATEGORY_TEXT: Record<string, string> = {
  conversation: "Conversation",
  outil: "Outil",
  hermes: "Hermes",
  approbation: "Approbation",
  voix: "Voix",
  systeme: "Système",
  securite: "Sécurité",
};

export default function ActivityDrawer({ onClose }: { onClose: () => void }) {
  const a = useAtlas();
  const [open, setOpen] = useState<number | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    a.loadActivity().catch(() => undefined);
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const events = a.activity.slice().reverse();
  const limited = a.hermes && a.hermes.mode === "chat_completions";
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Activité d'Atlas" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h2>Activité réelle</h2>
          <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label="Fermer le panneau d'activité"><CloseIcon /></button>
        </div>
        <p className="muted small drawer-intro">
          Seuls les événements réellement observés sont listés : étapes de l'interface et actions signalées par Hermes.
          Aucun raisonnement interne n'est affiché.
          {limited && " Mode compatibilité : Hermes ne signale que le début des outils (ni fin, ni approbation)."}
        </p>
        {events.length === 0 && <p className="empty">Aucune activité pour l'instant.</p>}
        <ol className="activity-list">
          {events.map((ev) => (
            <li key={ev.id} className={`act act-${ev.status}`}>
              <button className="act-row" onClick={() => setOpen(open === ev.id ? null : ev.id)} aria-expanded={open === ev.id} disabled={!ev.detail}>
                <span className="act-time">{fmt(ev.ts * 1000, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                <span className="act-cat">{CATEGORY_TEXT[ev.category] || ev.category}</span>
                <span className="act-label">{ev.label}</span>
                <span className={`act-status st-${ev.status}`}>
                  {STATUS_TEXT[ev.status] || ev.status}
                  {ev.duration_ms != null && ` · ${ev.duration_ms < 1000 ? ev.duration_ms + " ms" : (ev.duration_ms / 1000).toFixed(1) + " s"}`}
                </span>
              </button>
              {open === ev.id && ev.detail && <div className="act-detail">{ev.detail}</div>}
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
