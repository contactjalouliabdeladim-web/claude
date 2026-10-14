import { useState } from "react";
import ActivityDrawer from "./components/ActivityDrawer";
import CenterStage from "./components/CenterStage";
import ChatPanel from "./components/ChatPanel";
import DayPanel from "./components/DayPanel";
import { ChatIcon, CloseIcon, ListIcon, PulseIcon } from "./components/Icons";
import Login from "./components/Login";
import BusinessDashboard from "./components/BusinessDashboard";
import SettingsDialog from "./components/SettingsDialog";
import TopBar from "./components/TopBar";
import { AtlasProvider, useAtlas } from "./controller";

function Shell() {
  const a = useAtlas();
  const [drawer, setDrawer] = useState(false);
  const [settings, setSettings] = useState(false);
  const [mobileTab, setMobileTab] = useState<"atlas" | "day">("atlas");
  const [business, setBusiness] = useState(false);

  if (a.phase === "loading") {
    return (
      <main className="splash" aria-busy="true">
        <div className="login-orb" aria-hidden="true" />
        <p className="muted">Connexion à Atlas…</p>
      </main>
    );
  }
  if (a.phase === "login") return <Login onDone={a.start} />;
  if (a.phase === "unreachable") {
    return (
      <main className="splash">
        <div className="login-orb dim" aria-hidden="true" />
        <h1>Atlas est injoignable</h1>
        <p className="muted">Le PC Windows doit être allumé et Atlas lancé (double-clic sur « Atlas.cmd »).</p>
        <button className="btn btn-gold" onClick={a.start}>Réessayer</button>
      </main>
    );
  }

  return (
    <div className={`app ${a.reduceMotion ? "reduce-motion" : ""} tab-${mobileTab}`}>
      {a.boot && a.boot.demo && (
        <div className="demo-banner" role="note">MODE DÉMONSTRATION — réponses simulées, Hermes n'est pas utilisé.</div>
      )}
      <TopBar onActivity={() => setDrawer(true)} onSettings={() => setSettings(true)} business={business} onBusiness={() => setBusiness((v) => !v)} />
      {business ? <BusinessDashboard /> : <div className="layout">
        <DayPanel />
        <CenterStage />
        <ChatPanel />
      </div>}
      <nav className="mobile-nav" aria-label="Navigation">
        <button className={mobileTab === "atlas" ? "active" : ""} onClick={() => setMobileTab("atlas")} aria-pressed={mobileTab === "atlas"}>
          <ChatIcon size={18} /> Atlas
        </button>
        <button className={mobileTab === "day" ? "active" : ""} onClick={() => setMobileTab("day")} aria-pressed={mobileTab === "day"}>
          <ListIcon size={18} /> Journée
        </button>
        <button onClick={() => setDrawer(true)}>
          <PulseIcon size={18} /> Activité
        </button>
      </nav>
      {drawer && <ActivityDrawer onClose={() => setDrawer(false)} />}
      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
      <div className="toasts" aria-live="assertive">
        {a.toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
            <span>{t.text}</span>
            <button className="icon-btn sm" onClick={() => a.dismiss(t.id)} aria-label="Fermer le message">
              <CloseIcon size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AtlasProvider>
      <Shell />
    </AtlasProvider>
  );
}
