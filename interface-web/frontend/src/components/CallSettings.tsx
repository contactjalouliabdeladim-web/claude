import { useEffect, useState } from "react";
import { api } from "../api";
import { useAtlas } from "../controller";
import type { CallsStatus, Preferences } from "../types";

const SLOT_LABEL: Record<string, string> = { morning: "Appel du matin", afternoon: "Point de l'après-midi", evening: "Bilan du soir" };

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="set-row">
      <div className="set-label">{label}</div>
      <div className="set-value">{children}</div>
    </div>
  );
}

/** Réglages > Appels : horaires, sonnerie ntfy, adresse Tailscale, test. */
export default function CallSettings() {
  const a = useAtlas();
  const p = a.prefs!;
  const [status, setStatus] = useState<CallsStatus | null>(null);
  const [url, setUrl] = useState(p.call_public_url);
  const [testing, setTesting] = useState(false);

  const load = () => api<CallsStatus>("/api/calls").then(setStatus).catch(() => setStatus(null));
  useEffect(() => { load(); }, [p]);

  const save = async (patch: Partial<Preferences>) => {
    try {
      await a.updatePrefs(patch);
    } catch (e) {
      a.toast({ kind: "error", text: (e as Error).message });
    }
  };
  const time = (key: keyof Preferences, label: string) => (
    <input type="time" value={String(p[key])} aria-label={label} onChange={(e) => e.target.value && save({ [key]: e.target.value } as Partial<Preferences>)} />
  );
  const test = async () => {
    setTesting(true);
    try {
      await api("/api/calls/test", { method: "POST" });
      a.toast({ kind: "success", text: "Sonnerie envoyée : ton téléphone devrait sonner dans quelques secondes." });
    } catch (e) {
      a.toast({ kind: "error", text: (e as Error).message }, 10000);
    } finally {
      setTesting(false);
    }
  };
  const next = status && status.prochain;

  return (
    <>
      <Row label="Appels d'Atlas">
        <label className="check">
          <input type="checkbox" checked={p.calls_enabled} onChange={(e) => save({ calls_enabled: e.target.checked })} />
          Atlas m'appelle (sonnerie sur le téléphone)
        </label>
        {next && <p className="muted small">Prochain : {SLOT_LABEL[next.slot]} — {next.quand} à {next.time} ({next.why}).</p>}
        {status && status.coucher && <p className="muted small">Dernier « bonne nuit » noté : {status.coucher.local}.</p>}
      </Row>
      <Row label="Appel du matin">
        <div className="inline-form">Couché avant {time("call_bedtime_cutoff", "Heure limite de coucher")} → appel à {time("call_early", "Appel tôt")}</div>
        <div className="inline-form">sinon (ou « bonne nuit » non dit) → {time("call_late", "Appel tard")}</div>
      </Row>
      <Row label="Après-midi">
        <div className="inline-form">
          <label className="check"><input type="checkbox" checked={p.call_afternoon_enabled} onChange={(e) => save({ call_afternoon_enabled: e.target.checked })} /> Point court à</label>
          {time("call_afternoon", "Heure de l'appel de l'après-midi")}
        </div>
      </Row>
      <Row label="Soir">
        <div className="inline-form">
          <label className="check"><input type="checkbox" checked={p.call_evening_enabled} onChange={(e) => save({ call_evening_enabled: e.target.checked })} /> Bilan à</label>
          {time("call_evening", "Heure du bilan du soir")}
        </div>
        <p className="muted small">Sans réponse : une seule relance 15 minutes plus tard.</p>
      </Row>
      <Row label="Sonnerie (ntfy)">
        <div>Sujet secret à saisir dans l'appli ntfy :</div>
        <code className="wrap">{p.ntfy_topic}</code>
        <p className="muted small">Serveur : {p.ntfy_server}. Ne partage pas ce sujet : quiconque le connaît peut te faire sonner.</p>
      </Row>
      <Row label="Adresse du PC (Tailscale)">
        <div className="inline-form">
          <input value={url} placeholder="https://hp-scaleo.xxxx.ts.net" onChange={(e) => setUrl(e.target.value)} aria-label="Adresse HTTPS Tailscale" />
          <button className="btn btn-gold btn-sm" onClick={() => save({ call_public_url: url.trim() })} disabled={url === p.call_public_url}>Enregistrer</button>
        </div>
        <p className="muted small">C'est la page que la sonnerie ouvre sur ton téléphone ({(p.call_public_url || "http://localhost:8765") + "/appel"}).</p>
      </Row>
      <Row label="">
        <button className="btn btn-gold" onClick={test} disabled={testing}>{testing ? "Envoi…" : "Tester l'appel maintenant"}</button>
      </Row>
    </>
  );
}
