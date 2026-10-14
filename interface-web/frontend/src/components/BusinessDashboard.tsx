import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { useAtlas } from "../controller";
import { fmt } from "../time";
import type { FinanceOverview, FinanceTransaction } from "../types";

type Currency = "MAD" | "EUR";
type Kind = "income" | "expense";

function money(cents: number, currency: Currency) {
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency, maximumFractionDigits: 2 }).format(cents / 100);
}

function ClockCard({ zone, label }: { zone: string; label: string }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <div className="biz-clock"><span>{label}</span><strong>{new Intl.DateTimeFormat("fr-FR", { timeZone: zone, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(now)}</strong><small>{new Intl.DateTimeFormat("fr-FR", { timeZone: zone, weekday: "long", day: "numeric", month: "long" }).format(now)}</small></div>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone: string }) {
  return <div className={`biz-metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

export default function BusinessDashboard() {
  const a = useAtlas();
  const [data, setData] = useState<FinanceOverview | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [kind, setKind] = useState<Kind>("income");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState<Currency>("MAD");
  const [date, setDate] = useState("");
  const [client, setClient] = useState("");
  const [category, setCategory] = useState("");
  const [note, setNote] = useState("");

  const refresh = async () => {
    try {
      const next = await api<FinanceOverview>("/api/finance/overview");
      setData(next);
      setDate((current) => current || next.date);
      setError("");
    } catch (e) {
      setError((e as Error).message || "Chargement des données financières impossible.");
    }
  };
  useEffect(() => { refresh(); }, [a.financeVersion]);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const value = Number(amount.replace(",", "."));
    if (!Number.isFinite(value) || value <= 0 || !date) return;
    setSaving(true);
    try {
      await api<FinanceTransaction>("/api/finance/transactions", {
        method: "POST",
        json: { kind, amount_cents: Math.round(value * 100), currency, date, client, category, note },
      });
      setAmount(""); setClient(""); setCategory(""); setNote("");
      await refresh();
    } catch (e) {
      setError((e as Error).message || "Enregistrement impossible.");
    } finally { setSaving(false); }
  };

  const values = (period: "today" | "month" | "all_time", code: Currency) =>
    data?.[period][code] || { income_cents: 0, expense_cents: 0, net_cents: 0 };
  const recent = data?.transactions.slice(0, 12) || [];

  return <main className="business-page">
    <header className="business-header">
      <div><span className="biz-kicker">MASSAR · PILOTAGE</span><h1>Vue d’ensemble business</h1><p>Le cockpit de ton activité : argent encaissé, dépenses et rythme de travail.</p></div>
      <div className="business-clocks"><ClockCard zone="Africa/Casablanca" label="Casablanca"/><ClockCard zone="Europe/Rome" label="Rome"/></div>
    </header>

    <section className="biz-goal" aria-label="Objectif business">
      <div><span className="biz-kicker">CAP DES 4 PROCHAINS MOIS</span><strong>120 000 DH</strong><p>Objectif de bénéfice net cumulé. Le tableau sépare les encaissements des dépenses ; il ne prétend pas calculer la comptabilité complète.</p></div>
      <span className="biz-goal-badge">Suivi en cours</span>
    </section>

    <section className="biz-section" aria-labelledby="biz-today"><div className="biz-section-title"><div><span className="biz-kicker">AUJOURD’HUI</span><h2 id="biz-today">Mouvements du jour</h2></div><span>{data ? fmt(new Date(`${data.date}T12:00:00`).getTime(), { weekday: "long", day: "numeric", month: "long" }) : "Chargement…"}</span></div>
      <div className="biz-metrics">{(["MAD", "EUR"] as Currency[]).map((code) => <div className="biz-currency-group" key={code}><h3>{code === "MAD" ? "Dirhams" : "Euros"}</h3><Metric label="Encaissé" value={money(values("today", code).income_cents, code)} tone="income"/><Metric label="Dépensé" value={money(values("today", code).expense_cents, code)} tone="expense"/><Metric label="Solde du jour" value={money(values("today", code).net_cents, code)} tone="net"/></div>)}</div>
    </section>

    <section className="biz-section" aria-labelledby="biz-month"><div className="biz-section-title"><div><span className="biz-kicker">DEPUIS LE 1ER DU MOIS</span><h2 id="biz-month">Tendance mensuelle</h2></div></div>
      <div className="biz-metrics">{(["MAD", "EUR"] as Currency[]).map((code) => <div className="biz-currency-group" key={code}><h3>{code}</h3><Metric label="Entrées encaissées" value={money(values("month", code).income_cents, code)} tone="income"/><Metric label="Sorties enregistrées" value={money(values("month", code).expense_cents, code)} tone="expense"/><Metric label="Solde net de trésorerie" value={money(values("month", code).net_cents, code)} tone="net"/></div>)}</div>
    </section>

    <section className="biz-section" aria-labelledby="biz-lifetime"><div className="biz-section-title"><div><span className="biz-kicker">CUMUL DEPUIS LE DÉMARRAGE DU SUIVI</span><h2 id="biz-lifetime">Vue cumulée</h2></div></div>
      <div className="biz-metrics">{(["MAD", "EUR"] as Currency[]).map((code) => <div className="biz-currency-group" key={code}><h3>{code}</h3><Metric label="Total encaissé" value={money(values("all_time", code).income_cents, code)} tone="income"/><Metric label="Total dépensé" value={money(values("all_time", code).expense_cents, code)} tone="expense"/><Metric label="Solde de trésorerie" value={money(values("all_time", code).net_cents, code)} tone="net"/></div>)}</div>
    </section>

    <section className="biz-section" aria-labelledby="biz-clients"><div className="biz-section-title"><div><span className="biz-kicker">RELATION CLIENT</span><h2 id="biz-clients">Portefeuille actif</h2></div><span>3 clients actifs</span></div>
      <div className="biz-clients">{[
        ["Rome Arab Tours", "Marketing digital · ads · contenu · suivi des prospects"],
        ["Kick-Off TV", "Publicité · création de contenu"],
        ["Ecologia", "Espagne · publicité · création de contenu"],
      ].map(([name, detail]) => <article className="biz-client" key={name}><span className="biz-client-dot"/><div><strong>{name}</strong><small>{detail}</small></div><span className="biz-client-state">Actif</span></article>)}</div>
    </section>

    <div className="biz-columns">
      <section className="biz-panel" aria-labelledby="biz-add-title"><span className="biz-kicker">MISE À JOUR DU SUIVI</span><h2 id="biz-add-title">Ajouter un mouvement</h2><p className="biz-muted">Saisis les rentrées réellement reçues ou les dépenses réellement payées.</p>
        <form className="biz-form" onSubmit={add}>
          <div className="biz-kind" role="group" aria-label="Type de mouvement"><button type="button" className={kind === "income" ? "selected" : ""} onClick={() => setKind("income")}>＋ Encaissement</button><button type="button" className={kind === "expense" ? "selected expense-selected" : ""} onClick={() => setKind("expense")}>− Dépense</button></div>
          <label>Montant<input required type="number" min="0.01" step="0.01" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00"/></label>
          <div className="biz-form-row"><label>Devise<select value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}><option value="MAD">DH · MAD</option><option value="EUR">€ · EUR</option></select></label><label>Date<input required type="date" value={date} onChange={(e) => setDate(e.target.value)}/></label></div>
          <label>Client / source<input maxLength={100} value={client} onChange={(e) => setClient(e.target.value)} placeholder="Ex. Rome Arab Tours"/></label>
          <label>Catégorie<input maxLength={80} value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Ex. prestation, logiciel…"/></label>
          <label>Note<input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Référence ou détail utile"/></label>
          <button className="btn btn-gold biz-submit" type="submit" disabled={saving || !amount}>{saving ? "Enregistrement…" : "Enregistrer le mouvement"}</button>
        </form>
        {error && <p className="biz-error" role="alert">{error}</p>}
      </section>

      <section className="biz-panel" aria-labelledby="biz-log-title"><div className="biz-section-title"><div><span className="biz-kicker">JOURNAL FINANCIER</span><h2 id="biz-log-title">Derniers mouvements</h2></div><span>{data ? `${data.transactions.length} affichés` : ""}</span></div>
        {recent.length === 0 ? <div className="biz-empty">Aucun mouvement enregistré pour l’instant. Ajoute une rentrée ou une dépense pour démarrer ton suivi.</div> : <div className="biz-table-wrap"><table className="biz-table"><thead><tr><th>Date</th><th>Type / source</th><th>Montant</th></tr></thead><tbody>{recent.map((row) => <tr key={row.id}><td>{row.date}</td><td><strong>{row.kind === "income" ? "Encaissement" : "Dépense"}</strong><small>{row.client || row.category || row.note || "—"}</small></td><td className={row.kind === "income" ? "biz-amount-in" : "biz-amount-out"}>{row.kind === "income" ? "+" : "−"}{money(row.amount_cents, row.currency as Currency)}</td></tr>)}</tbody></table></div>}
      </section>
    </div>

    <section className="biz-sync"><div><span className="biz-kicker">SYNCHRONISATION</span><h2>Google Sheets</h2><p>Les mouvements sont enregistrés dans Atlas sur ton PC pour le moment. La liaison au classeur ne sera active qu’après l’autorisation Google Sheets.</p></div><span className="biz-sync-state">Non connecté</span></section>
    <p className="biz-footnote">Les montants MAD et EUR restent séparés. « Solde net » = encaissements moins dépenses saisies ; ce n’est pas un bénéfice comptable ni un relevé bancaire.</p>
  </main>;
}
