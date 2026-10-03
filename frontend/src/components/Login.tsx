import { useState } from "react";
import { api } from "../api";

function guessDevice(): string {
  const ua = navigator.userAgent;
  if (/Macintosh|Mac OS X/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "PC Windows";
  if (/iPhone|iPad/.test(ua)) return "iPhone/iPad";
  if (/Android/.test(ua)) return "Android";
  return "Appareil";
}

export default function Login({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [device, setDevice] = useState(guessDevice());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <main className="login">
      <div className="login-card">
        <div className="login-orb" aria-hidden="true" />
        <h1>Atlas</h1>
        <p className="muted">Saisis le code d'accès affiché par Atlas sur le PC Windows (fichier « CODE-ACCES.txt »).</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            setError("");
            try {
              await api("/api/auth/login", { method: "POST", json: { code, device_label: device } });
              onDone();
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label htmlFor="code">Code d'accès</label>
          <input id="code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="XXXX-XXXX-XXXX" autoFocus />
          <label htmlFor="device">Nom de cet appareil</label>
          <input id="device" value={device} maxLength={60} onChange={(e) => setDevice(e.target.value)} />
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="btn btn-gold btn-block" disabled={busy || code.replace(/[^A-Za-z0-9]/g, "").length < 8}>
            {busy ? "Vérification…" : "Se connecter"}
          </button>
        </form>
        {window.isSecureContext === false && (
          <p className="muted small">
            Connexion non sécurisée : le chat fonctionnera, mais le navigateur bloquera le micro. Utilise l'adresse HTTPS (voir le guide Mac).
          </p>
        )}
      </div>
    </main>
  );
}
