import { useAtlas } from "../controller";
import type { Cockpit } from "../types";

const TONE: Record<Cockpit["etat"], string> = {
  dans_les_temps: "ok",
  atteint: "ok",
  leger_retard: "warn",
  gros_retard: "bad",
};

const STATE_LABEL: Record<Cockpit["etat"], string> = {
  dans_les_temps: "Dans les temps",
  atteint: "Objectif atteint",
  leger_retard: "Léger retard",
  gros_retard: "Gros retard",
};

function dh(v: number | null) {
  return v === null ? "—" : `${Math.round(v).toLocaleString("fr-FR")} DH`;
}

/** Jauge du Cockpit 120K, sous l'orbe : progression, rythme nécessaire, avance ou retard. */
export function CockpitGauge({ compact = false }: { compact?: boolean }) {
  const a = useAtlas();
  const c = a.cockpit;
  if (!c) return null;
  const pct = Math.max(0, Math.min(100, c.pourcentage));
  const expected = Math.max(0, Math.min(100, (c.attendu_a_date_mad / c.objectif_mad) * 100));
  const tone = TONE[c.etat];
  const detail = [
    `${c.pourcentage.toLocaleString("fr-FR")} % atteint · reste ${dh(c.reste_mad)}`,
    `rythme réel ${dh(c.rythme_reel_semaine_mad)}/semaine`,
    `${c.jours_restants} jours restants (jusqu'au ${new Date(`${c.fin}T12:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "long" })})`,
  ].join(" · ");
  return (
    <div className={`cockpit-gauge tone-${tone} ${compact ? "compact" : ""}`} role="group" aria-label="Cockpit 120K" title={detail}>
      <div className="cockpit-head">
        <span className="cockpit-kicker">COCKPIT {Math.round(c.objectif_mad / 1000)}K</span>
        <span className="cockpit-state">{STATE_LABEL[c.etat]}</span>
      </div>
      <div className="cockpit-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label="Progression vers l'objectif">
        <div className="cockpit-fill" style={{ width: `${pct}%` }} />
        <div className="cockpit-marker" style={{ left: `${expected}%` }} title="Où tu devrais être aujourd'hui" />
      </div>
      <div className="cockpit-summary">{c.resume}</div>
      {!compact && <div className="cockpit-detail">{detail}</div>}
    </div>
  );
}

/** « Action du jour qui rapporte », en tête du tableau de bord. */
export function MoneyAction() {
  const a = useAtlas();
  const action = a.cockpit && a.cockpit.action_du_jour;
  return (
    <section className={`card money-action ${action ? "" : "empty"}`} aria-label="Action du jour qui rapporte">
      <span className="cockpit-kicker">ACTION DU JOUR QUI RAPPORTE</span>
      {action ? (
        <>
          <strong>{action.action}</strong>
          {action.pourquoi && <p>{action.pourquoi}</p>}
        </>
      ) : (
        <p>Pas encore fixée : Atlas la propose pendant la routine (ou l'appel) du matin.</p>
      )}
    </section>
  );
}
