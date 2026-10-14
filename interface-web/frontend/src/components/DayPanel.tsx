import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { useAtlas } from "../controller";
import { dayKey, fmt } from "../time";
import type { Note, Priority } from "../types";
import { CheckIcon, DownIcon, EditIcon, PlusIcon, ShareIcon, TrashIcon, UpIcon } from "./Icons";
import Tasks from "./Tasks";

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}


export function Clock() {
  const a = useAtlas();
  const tz = (a.prefs && a.prefs.timezone) || "Africa/Casablanca";
  const [now, setNow] = useState(() => new Date());
  const lastDay = useRef("");
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  void a.clockOffset; // nouveau rendu dès que le PC donne le décalage
  const day = dayKey(now.getTime());
  useEffect(() => {
    // Changement de date : on recharge les priorités du nouveau jour (aucun appel au modèle).
    if (lastDay.current && lastDay.current !== day) a.loadPriorities().catch(() => undefined);
    lastDay.current = day;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day]);
  const time = fmt(now.getTime(), { hour: "2-digit", minute: "2-digit" });
  const date = capitalize(fmt(now.getTime(), { weekday: "long", day: "numeric", month: "long", year: "numeric" }));
  const hour = parseInt(fmt(now.getTime(), { hour: "2-digit", hourCycle: "h23" } as Intl.DateTimeFormatOptions), 10);
  const greeting = hour < 5 ? "Bonne nuit" : hour < 12 ? "Bonjour" : hour < 18 ? "Bon après-midi" : "Bonsoir";
  const todayStr = a.tasks.day;
  const doneToday = a.tasks.done.filter((t) => t.completed_at && dayKey(t.completed_at * 1000) === day).length;
  const remaining = a.tasks.today.length;
  const total = remaining + doneToday;
  const ratio = total ? doneToday / total : 0;
  const late = a.tasks.today.filter((t) => t.due_date && todayStr && t.due_date < todayStr).length;
  const prioDone = a.priorities.filter((p) => p.status === "done").length;
  const R = 26;
  const C = 2 * Math.PI * R;
  return (
    <section className="clock card hero" aria-label="Heure et date">
      <div className="hero-text">
        <div className="greeting">{greeting}</div>
        <div className="clock-time">{time}</div>
        <div className="clock-date">{date}</div>
        <div className="clock-tz">{tz.replace(/_/g, " ")}</div>
      </div>
      <div className="day-progress" title={`${doneToday} tâche(s) faite(s) sur ${total} aujourd'hui`}>
        <svg viewBox="0 0 64 64" width="72" height="72" aria-hidden="true">
          <circle cx="32" cy="32" r={R} className="ring-bg" />
          <circle cx="32" cy="32" r={R} className="ring-fg" strokeDasharray={C} strokeDashoffset={C * (1 - ratio)} transform="rotate(-90 32 32)" />
        </svg>
        <div className="ring-text">
          <strong>{doneToday}</strong>/{total}
        </div>
      </div>
      <ul className="hero-stats">
        <li><strong>{remaining}</strong> à faire</li>
        {late > 0 && <li className="late"><strong>{late}</strong> en retard</li>}
        <li><strong>{prioDone}/{a.priorities.length}</strong> priorités</li>
      </ul>
    </section>
  );
}

const STATUS_LABEL: Record<Priority["status"], string> = { todo: "À faire", doing: "En cours", done: "Terminé" };
const NEXT_STATUS: Record<Priority["status"], Priority["status"]> = { todo: "doing", doing: "done", done: "todo" };

function PriorityForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: { title: string; description: string; due: string };
  onSave: (v: { title: string; description: string; due: string }) => Promise<void>;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(initial.title);
  const [description, setDescription] = useState(initial.description);
  const [due, setDue] = useState(initial.due);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <form
      className="prio-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim() || saving) return;
        setSaving(true);
        try {
          await onSave({ title: title.trim(), description: description.trim(), due });
        } finally {
          setSaving(false);
        }
      }}
    >
      <label className="sr-only" htmlFor="prio-title">Titre de la priorité</label>
      <input id="prio-title" ref={ref} value={title} maxLength={200} placeholder="Ma priorité…" onChange={(e) => setTitle(e.target.value)} />
      <label className="sr-only" htmlFor="prio-desc">Description</label>
      <textarea id="prio-desc" value={description} maxLength={2000} rows={2} placeholder="Détails (facultatif)" onChange={(e) => setDescription(e.target.value)} />
      <label className="field-inline">
        <span>Échéance</span>
        <input type="time" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Échéance (heure, facultative)" />
      </label>
      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>Annuler</button>
        <button type="submit" className="btn btn-gold" disabled={!title.trim() || saving}>Enregistrer</button>
      </div>
    </form>
  );
}

export function Priorities() {
  const a = useAtlas();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const items = a.priorities;

  useEffect(() => {
    if (a.priorityDraft) {
      setAdding(true);
      setEditing(null);
    }
  }, [a.priorityDraft]);

  const fail = (e: unknown) => a.toast({ kind: "error", text: (e as Error).message });

  const save = async (v: { title: string; description: string; due: string }, id?: string) => {
    try {
      if (id) await api(`/api/priorities/${id}`, { method: "PATCH", json: { title: v.title, description: v.description, due: v.due || null, clear_due: !v.due } });
      else await api("/api/priorities", { method: "POST", json: { title: v.title, description: v.description, due: v.due || null, day: a.prioritiesDay || null } });
      await a.loadPriorities();
      setAdding(false);
      setEditing(null);
      a.setPriorityDraft(null);
    } catch (e) {
      fail(e);
    }
  };

  const move = async (index: number, delta: number) => {
    const ids = items.map((p) => p.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    try {
      await api("/api/priorities/reorder", { method: "POST", json: { day: a.prioritiesDay, ids } });
      await a.loadPriorities();
    } catch (e) {
      fail(e);
    }
  };

  return (
    <section className="panel-section card" aria-labelledby="prio-heading">
      <div className="section-head">
        <h2 id="prio-heading">Mes 3 priorités</h2>
        <span className="muted small">{items.length}/3</span>
      </div>
      {items.length === 0 && !adding && <p className="empty">Aucune priorité pour aujourd'hui. Choisis-en jusqu'à trois.</p>}
      <ol className="prio-list">
        {items.map((p, i) =>
          editing === p.id ? (
            <li key={p.id}>
              <PriorityForm initial={{ title: p.title, description: p.description, due: p.due || "" }} onSave={(v) => save(v, p.id)} onCancel={() => setEditing(null)} />
            </li>
          ) : (
            <li key={p.id} className={`prio prio-${p.status}`}>
              <button
                className="prio-check"
                onClick={() => api(`/api/priorities/${p.id}`, { method: "PATCH", json: { status: p.status === "done" ? "todo" : "done" } }).then(a.loadPriorities, fail)}
                aria-label={p.status === "done" ? `Marquer « ${p.title} » comme non terminée` : `Marquer « ${p.title} » comme terminée`}
                aria-pressed={p.status === "done"}
              >
                {p.status === "done" && <CheckIcon size={16} />}
              </button>
              <div className="prio-body">
                <div className="prio-title">{p.title}</div>
                {p.description && <div className="prio-desc">{p.description}</div>}
                <div className="prio-meta">
                  <button
                    className={`status-chip status-${p.status}`}
                    onClick={() => api(`/api/priorities/${p.id}`, { method: "PATCH", json: { status: NEXT_STATUS[p.status] } }).then(a.loadPriorities, fail)}
                    title="Changer le statut"
                    aria-label={`Statut : ${STATUS_LABEL[p.status]}. Passer à ${STATUS_LABEL[NEXT_STATUS[p.status]]}`}
                  >
                    {STATUS_LABEL[p.status]}
                  </button>
                  {p.due && <span className="muted small">avant {p.due}</span>}
                </div>
              </div>
              <div className="prio-tools">
                <button className="icon-btn sm" onClick={() => move(i, -1)} disabled={i === 0} aria-label={`Monter « ${p.title} »`}><UpIcon size={16} /></button>
                <button className="icon-btn sm" onClick={() => move(i, 1)} disabled={i === items.length - 1} aria-label={`Descendre « ${p.title} »`}><DownIcon size={16} /></button>
                <button className="icon-btn sm" onClick={() => setEditing(p.id)} aria-label={`Modifier « ${p.title} »`}><EditIcon size={16} /></button>
                <button
                  className="icon-btn sm"
                  onClick={() => {
                    if (window.confirm(`Supprimer la priorité « ${p.title} » ?`))
                      api(`/api/priorities/${p.id}`, { method: "DELETE" }).then(a.loadPriorities, fail);
                  }}
                  aria-label={`Supprimer « ${p.title} »`}
                >
                  <TrashIcon size={16} />
                </button>
              </div>
            </li>
          ),
        )}
      </ol>
      {adding ? (
        <PriorityForm
          initial={{ title: a.priorityDraft?.title || "", description: a.priorityDraft?.description || "", due: "" }}
          onSave={(v) => save(v)}
          onCancel={() => {
            setAdding(false);
            a.setPriorityDraft(null);
          }}
        />
      ) : (
        items.length < 3 && (
          <button className="btn btn-ghost btn-block" onClick={() => setAdding(true)}>
            <PlusIcon size={16} /> Ajouter une priorité
          </button>
        )
      )}
    </section>
  );
}

function formatUpdated(ts: number) {
  const ms = ts * 1000;
  const hm = fmt(ms, { hour: "2-digit", minute: "2-digit" });
  return dayKey(ms) === dayKey(Date.now()) ? `aujourd'hui ${hm}` : fmt(ms, { day: "numeric", month: "short" }) + " " + hm;
}

function NoteEditor({ note, onClose }: { note: Note; onClose: () => void }) {
  const a = useAtlas();
  const [text, setText] = useState(note.content);
  const [status, setStatus] = useState<"saved" | "dirty" | "saving" | "error" | "conflict">("saved");
  const base = useRef(note.updated_at);
  const timer = useRef<number | null>(null);
  const latest = useRef(text);
  latest.current = text;

  const save = async () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setStatus("saving");
    try {
      const updated = await api<Note>(`/api/notes/${note.id}`, {
        method: "PATCH",
        json: { content: latest.current, expected_updated_at: base.current },
        headers: { "X-Atlas-Device": a.me },
      });
      base.current = updated.updated_at;
      a.setNotes((list) => list.map((n) => (n.id === updated.id ? updated : n)));
      setStatus(latest.current === updated.content ? "saved" : "dirty");
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 409 ? "conflict" : "error");
    }
  };

  useEffect(() => () => {
    if (timer.current) {
      window.clearTimeout(timer.current);
      save();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const label = { saved: "Enregistré", dirty: "Modifié…", saving: "Enregistrement…", error: "Erreur d'enregistrement", conflict: "Modifiée sur un autre appareil" }[status];
  return (
    <div className="note-editor">
      <label className="sr-only" htmlFor={`note-${note.id}`}>Contenu de la note</label>
      <textarea
        id={`note-${note.id}`}
        value={text}
        rows={5}
        maxLength={20000}
        autoFocus
        onChange={(e) => {
          setText(e.target.value);
          setStatus("dirty");
          if (timer.current) window.clearTimeout(timer.current);
          timer.current = window.setTimeout(save, 900);
        }}
      />
      <div className="note-editor-foot">
        <span className={`save-state save-${status}`} role="status">{label}</span>
        {status === "conflict" && (
          <button className="btn btn-ghost btn-sm" onClick={() => a.loadNotes().then(onClose)}>Recharger</button>
        )}
        {status === "error" && <button className="btn btn-ghost btn-sm" onClick={save}>Réessayer</button>}
        <button className="btn btn-ghost btn-sm" onClick={async () => { if (status === "dirty") await save(); onClose(); }}>Fermer</button>
      </div>
    </div>
  );
}

export function Notes() {
  const a = useAtlas();
  const [editing, setEditing] = useState<string | null>(null);
  const create = async () => {
    try {
      const n = await api<Note>("/api/notes", { method: "POST", json: { content: "" }, headers: { "X-Atlas-Device": a.me } });
      a.setNotes((list) => [n, ...list]);
      setEditing(n.id);
    } catch (e) {
      a.toast({ kind: "error", text: (e as Error).message });
    }
  };
  return (
    <section className="panel-section card notes" aria-labelledby="notes-heading">
      <div className="section-head">
        <h2 id="notes-heading">Notes rapides</h2>
        <button className="icon-btn sm" onClick={create} aria-label="Nouvelle note" title="Nouvelle note"><PlusIcon size={16} /></button>
      </div>
      {a.notes.length === 0 && <p className="empty">Aucune note. Elles restent sur le PC Windows, accessibles depuis le Mac.</p>}
      <ul className="note-list">
        {a.notes.map((n) =>
          editing === n.id ? (
            <li key={n.id} className="note note-open">
              <NoteEditor note={n} onClose={() => setEditing(null)} />
            </li>
          ) : (
            <li key={n.id} className="note">
              <button className="note-preview" onClick={() => setEditing(n.id)} aria-label="Modifier la note">
                <span className="note-text">{n.content.trim() || <em className="muted">Note vide</em>}</span>
                <span className="note-date">{formatUpdated(n.updated_at)}</span>
              </button>
              <div className="note-tools">
                <button
                  className={`icon-btn sm ${a.attachedNotes.indexOf(n.id) >= 0 ? "active" : ""}`}
                  onClick={() => {
                    if (!n.content.trim()) return;
                    a.setAttachedNotes((list) => (list.indexOf(n.id) >= 0 ? list.filter((x) => x !== n.id) : [...list, n.id].slice(-5)));
                  }}
                  aria-label="Utiliser dans la conversation"
                  title="Utiliser dans la conversation (jointe au prochain message)"
                >
                  <ShareIcon size={16} />
                </button>
                <button
                  className="icon-btn sm"
                  onClick={() => {
                    if (window.confirm("Supprimer cette note ?"))
                      api(`/api/notes/${n.id}`, { method: "DELETE", headers: { "X-Atlas-Device": a.me } })
                        .then(() => a.setNotes((list) => list.filter((x) => x.id !== n.id)))
                        .catch((e) => a.toast({ kind: "error", text: e.message }));
                  }}
                  aria-label="Supprimer la note"
                >
                  <TrashIcon size={16} />
                </button>
              </div>
            </li>
          ),
        )}
      </ul>
    </section>
  );
}

export default function DayPanel() {
  return (
    <aside className="day-panel" aria-label="Ma journée">
      <Clock />
      <Priorities />
      <Tasks />
      <Notes />
    </aside>
  );
}
