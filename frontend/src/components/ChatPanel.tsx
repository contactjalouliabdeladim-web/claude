import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useAtlas } from "../controller";
import type { Message } from "../types";
import { CheckIcon, CloseIcon, CopyIcon, FlagIcon, HistoryIcon, MicIcon, PlusIcon, SendIcon, StopIcon, TrashIcon, VolumeIcon } from "./Icons";
import Markdown from "./Markdown";

const ERROR_TEXT: Record<string, string> = {
  quota: "Limite d'utilisation du fournisseur atteinte. Tes données sont conservées ; réessaie plus tard. Aucun autre fournisseur n'est utilisé automatiquement.",
  provider_auth: "Le fournisseur du modèle refuse la connexion. Relance « hermes model » dans PowerShell pour te reconnecter.",
  provider: "Hermes n'a pas pu terminer cette réponse.",
  stream_lost: "La connexion avec Hermes a été perdue : la tâche a été interrompue. Rien n'a été relancé automatiquement.",
  auth: "Hermes refuse la clé d'Atlas (voir Réglages > Connexion).",
  unreachable: "Hermes ne répond pas. Le « hermes gateway » est-il lancé ?",
  busy: "Hermes est saturé. Réessaie dans un instant.",
  internal: "Erreur interne d'Atlas.",
};

function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy") ? resolve() : reject(new Error("copie refusée"));
    } finally {
      document.body.removeChild(ta);
    }
  });
}

function MessageItem({ m }: { m: Message }) {
  const a = useAtlas();
  const [copied, setCopied] = useState(false);
  const isUser = m.role === "user";
  const streaming = m.status === "streaming";
  const tz = (a.prefs && a.prefs.timezone) || undefined;
  let time: string;
  try {
    time = new Date(m.created_at * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: tz });
  } catch {
    time = new Date(m.created_at * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  return (
    <li className={`msg ${isUser ? "msg-user" : "msg-atlas"} msg-${m.status}`}>
      <div className="msg-head">
        <span className="msg-author">{isUser ? "Toi" : "Atlas"}</span>
        {isUser && m.meta.kind === "voice" && <span className="msg-tag" title="Message dicté"><MicIcon size={12} /> dicté</span>}
        <span className="msg-time">{time}</span>
      </div>
      <div className="msg-body">
        {m.content ? <Markdown text={m.content} /> : streaming ? <span className="typing" aria-label="Atlas écrit">•••</span> : null}
        {streaming && m.content && <span className="caret" aria-hidden="true" />}
      </div>
      {!isUser && m.status === "error" && (
        <div className="msg-error" role="alert">
          {ERROR_TEXT[m.meta.error_kind || "provider"] || ERROR_TEXT.provider}
          {m.meta.error && m.meta.error_kind !== "quota" && <div className="msg-error-detail">{m.meta.error}</div>}
        </div>
      )}
      {!isUser && m.status === "interrupted" && <div className="msg-error">{ERROR_TEXT.stream_lost}{m.meta.error ? "" : " (arrêt du serveur Atlas)"}</div>}
      {!isUser && m.status === "cancelled" && <div className="msg-note">Tâche interrompue à ta demande.</div>}
      {!isUser && !streaming && m.content && (
        <div className="msg-actions">
          <button
            className="icon-btn sm"
            onClick={() => copyText(m.content).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }, () => a.toast({ kind: "error", text: "Copie impossible." }))}
            aria-label="Copier la réponse"
            title="Copier"
          >
            {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
          </button>
          <button
            className="icon-btn sm"
            onClick={() => (a.speakingId === m.id ? a.stopSpeaking() : a.speak(m.id))}
            aria-label={a.speakingId === m.id ? "Couper la voix" : "Écouter cette réponse"}
            title={a.speakingId === m.id ? "Couper la voix" : "Écouter"}
            disabled={!!a.boot && !a.boot.voice.tts.available}
          >
            {a.speakingId === m.id ? <StopIcon size={15} /> : <VolumeIcon size={15} />}
          </button>
          <button
            className="icon-btn sm"
            onClick={() => {
              const sel = window.getSelection ? String(window.getSelection()) : "";
              const source = sel.trim() || m.content;
              const firstLine = source.split("\n").map((l) => l.replace(/^[-*\d.)\s#>]+/, "").trim()).find(Boolean) || "";
              a.setPriorityDraft({ title: firstLine.slice(0, 200), description: "" });
            }}
            aria-label="Créer une priorité à partir de cette réponse"
            title="Créer une priorité (sélectionne d'abord un passage si besoin)"
          >
            <FlagIcon size={15} />
          </button>
          {m.meta.runtime && m.meta.runtime.model && (
            <span className="msg-model" title="Modèle réellement utilisé, d'après Hermes">{m.meta.runtime.model}</span>
          )}
        </div>
      )}
    </li>
  );
}

function ConversationMenu({ onClose }: { onClose: () => void }) {
  const a = useAtlas();
  return (
    <div className="conv-menu" role="dialog" aria-label="Conversations">
      <div className="conv-menu-head">
        <strong>Conversations</strong>
        <button className="icon-btn sm" onClick={onClose} aria-label="Fermer"><CloseIcon size={16} /></button>
      </div>
      {a.conversations.length === 0 && <p className="empty">Aucune conversation.</p>}
      <ul>
        {a.conversations.map((c) => (
          <li key={c.id} className={c.id === a.activeConvId ? "active" : ""}>
            <button className="conv-item" onClick={() => { a.openConversation(c.id); onClose(); }}>
              <span className="conv-title">{c.title}</span>
              <span className="conv-date">{new Date(c.updated_at * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "short" })}</span>
            </button>
            <button
              className="icon-btn sm"
              onClick={() => {
                if (window.confirm(`Supprimer « ${c.title} » de l'interface ? (L'historique reste dans Hermes.)`))
                  a.deleteConversation(c.id).catch((e) => a.toast({ kind: "error", text: e.message }));
              }}
              aria-label={`Supprimer la conversation « ${c.title} »`}
            >
              <TrashIcon size={15} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ChatPanel() {
  const a = useAtlas();
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);
  const [menu, setMenu] = useState(false);
  const conv = a.conversations.find((c) => c.id === a.activeConvId);
  const activeHere = a.serverState.state !== "idle" && a.serverState.conversation_id === a.activeConvId;

  const onScroll = () => {
    const el = listRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [a.messages]);
  useEffect(() => {
    stick.current = true;
  }, [a.activeConvId]);

  // Zone de saisie qui grandit avec le texte.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(220, el.scrollHeight) + "px";
  }, [a.draft]);

  const submit = () => {
    if (!a.draft.trim() || a.sending || a.busy) return;
    stick.current = true;
    a.send(a.draft);
  };

  const notesAttached = a.notes.filter((n) => a.attachedNotes.indexOf(n.id) >= 0);

  return (
    <section className="chat-panel" aria-label="Conversation">
      <div className="chat-head">
        <button className="icon-btn" onClick={() => setMenu(!menu)} aria-label="Conversations précédentes" aria-expanded={menu} title="Conversations">
          <HistoryIcon />
        </button>
        <h2 className="chat-title" title={conv ? conv.title : ""}>{conv ? conv.title : "Nouvelle conversation"}</h2>
        <button className="icon-btn" onClick={() => a.newConversation().catch((e) => a.toast({ kind: "error", text: e.message }))} aria-label="Nouvelle conversation" title="Nouvelle conversation" disabled={a.sending}>
          <PlusIcon />
        </button>
        {menu && <ConversationMenu onClose={() => setMenu(false)} />}
      </div>

      <div className="chat-list" ref={listRef} onScroll={onScroll} aria-live="polite" aria-relevant="additions">
        {a.messages.length === 0 && !a.messagesLoading && (
          <div className="chat-empty">
            <p>Écris ou parle à Atlas.</p>
            <p className="muted small">Ta personnalité, ta mémoire et tes compétences restent celles d'Hermes.</p>
          </div>
        )}
        <ul>
          {a.messages.map((m) => (
            <MessageItem key={m.id} m={m} />
          ))}
        </ul>
      </div>

      {a.serverState.approval && (
        <div className="approval" role="alertdialog" aria-label="Approbation requise">
          <div className="approval-title">Atlas demande ton accord</div>
          {a.serverState.approval.description && <p>{a.serverState.approval.description}</p>}
          {a.serverState.approval.command && <pre className="md-pre"><code>{a.serverState.approval.command}</code></pre>}
          <div className="approval-actions">
            <button className="btn btn-outline" onClick={() => a.answerApproval("deny")}>Refuser</button>
            {a.serverState.approval.choices.indexOf("session") >= 0 && (
              <button className="btn btn-ghost" onClick={() => a.answerApproval("session")}>Autoriser pour cette session</button>
            )}
            <button className="btn btn-gold" onClick={() => a.answerApproval("once")}>Autoriser une fois</button>
          </div>
          <p className="muted small">Sans réponse, Hermes refuse automatiquement après son délai.</p>
        </div>
      )}

      {a.uncertain && (
        <div className="notice" role="alert">
          Envoi incertain (connexion perdue).
          <button className="btn btn-gold btn-sm" onClick={a.retryUncertain}>Réessayer sans doublon</button>
          <button className="btn btn-ghost btn-sm" onClick={a.dismissUncertain}>Ignorer</button>
        </div>
      )}

      {activeHere && (
        <div className="working-bar">
          <span className="working-dot" aria-hidden="true" />
          <span>{a.serverState.label}</span>
          {a.serverState.can_stop !== false && (
            <button className="btn btn-outline btn-sm" onClick={a.stopTask} aria-label="Arrêter la tâche en cours">
              <StopIcon size={14} /> Arrêter la tâche
            </button>
          )}
        </div>
      )}
      {a.busy && !activeHere && <div className="working-bar muted">Atlas répond dans une autre conversation…</div>}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {notesAttached.length > 0 && (
          <div className="attached">
            {notesAttached.map((n) => (
              <span key={n.id} className="chip">
                Note : {n.content.trim().slice(0, 32)}
                {n.content.trim().length > 32 ? "…" : ""}
                <button type="button" onClick={() => a.setAttachedNotes((l) => l.filter((x) => x !== n.id))} aria-label="Retirer la note">
                  <CloseIcon size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="composer-row">
          <label className="sr-only" htmlFor="composer">Message pour Atlas</label>
          <textarea
            id="composer"
            ref={inputRef}
            rows={1}
            value={a.draft}
            maxLength={20000}
            placeholder={a.busy ? "Atlas est occupé…" : "Écris à Atlas…"}
            title="Entrée pour envoyer, Maj+Entrée pour aller à la ligne"
            onChange={(e) => a.setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !(e.nativeEvent as any).isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button
            type="button"
            className="icon-btn composer-mic"
            onClick={a.localVoice === "listening" ? a.finishListening : a.startListening}
            aria-label={a.localVoice === "listening" ? "Terminer la dictée" : "Dicter un message"}
            disabled={a.localVoice === "transcribing" || (a.busy && a.localVoice !== "listening")}
          >
            {a.localVoice === "listening" ? <StopIcon /> : <MicIcon />}
          </button>
          <button type="submit" className="send-btn" disabled={!a.draft.trim() || a.sending || a.busy} aria-label="Envoyer">
            <SendIcon />
          </button>
        </div>
        <div className="composer-hint">Entrée : envoyer · Maj+Entrée : nouvelle ligne</div>
      </form>
    </section>
  );
}
