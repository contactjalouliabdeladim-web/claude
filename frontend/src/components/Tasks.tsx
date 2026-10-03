import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAtlas } from "../controller";
import type { Task } from "../types";
import { CheckIcon, EditIcon, PlusIcon, SparkIcon, TrashIcon } from "./Icons";

type Tab = "today" | "upcoming" | "done";

function addDays(iso: string, n: number): string {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function dueLabel(t: Task, today: string): { text: string; late: boolean } | null {
  if (!t.due_date) return t.due_time ? { text: t.due_time, late: false } : null;
  const time = t.due_time ? ` · ${t.due_time}` : "";
  if (t.due_date === today) return t.due_time ? { text: t.due_time, late: false } : null;
  if (today && t.due_date < today) return { text: "en retard" + time, late: true };
  if (today && t.due_date === addDays(today, 1)) return { text: "demain" + time, late: false };
  const d = new Date(t.due_date + "T12:00:00");
  return { text: d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" }) + time, late: false };
}

function TaskRow({ t, today, flash }: { t: Task; today: string; flash: boolean }) {
  const a = useAtlas();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(t.title);
  const fail = (e: unknown) => a.toast({ kind: "error", text: (e as Error).message });
  const due = dueLabel(t, today);
  const done = t.status === "done";
  if (editing) {
    return (
      <li className="task editing">
        <form
          className="task-edit"
          onSubmit={(e) => {
            e.preventDefault();
            if (!title.trim()) return;
            api(`/api/tasks/${t.id}`, { method: "PATCH", json: { title: title.trim() } }).then(() => setEditing(false), fail);
          }}
        >
          <input value={title} maxLength={200} autoFocus onChange={(e) => setTitle(e.target.value)} aria-label="Titre de la tâche" />
          <button className="btn btn-gold btn-sm" type="submit">OK</button>
          <button className="btn btn-ghost btn-sm" type="button" onClick={() => setEditing(false)}>Annuler</button>
        </form>
      </li>
    );
  }
  return (
    <li className={`task ${done ? "done" : ""} ${flash ? "flash" : ""}`}>
      <button
        className="task-check"
        aria-pressed={done}
        aria-label={done ? `Rouvrir « ${t.title} »` : `Marquer « ${t.title} » comme faite`}
        onClick={() => api(`/api/tasks/${t.id}`, { method: "PATCH", json: { status: done ? "todo" : "done" } }).catch(fail)}
      >
        {done && <CheckIcon size={14} />}
      </button>
      <div className="task-body">
        <span className="task-title">{t.title}</span>
        <span className="task-meta">
          {due && <span className={`chip-sm ${due.late ? "late" : ""}`}>{due.text}</span>}
          {t.source === "atlas" && (
            <span className="chip-sm atlas" title="Ajoutée par Atlas à ta demande">
              <SparkIcon size={11} /> Atlas
            </span>
          )}
          {t.notes && <span className="task-notes">{t.notes}</span>}
        </span>
      </div>
      <div className="task-tools">
        {!done && (
          <button className="icon-btn xs" onClick={() => setEditing(true)} aria-label={`Modifier « ${t.title} »`}>
            <EditIcon size={14} />
          </button>
        )}
        <button
          className="icon-btn xs"
          aria-label={`Supprimer « ${t.title} »`}
          onClick={() => window.confirm(`Supprimer « ${t.title} » ?`) && api(`/api/tasks/${t.id}`, { method: "DELETE" }).catch(fail)}
        >
          <TrashIcon size={14} />
        </button>
      </div>
    </li>
  );
}

export default function Tasks() {
  const a = useAtlas();
  const [tab, setTab] = useState<Tab>("today");
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState<"today" | "tomorrow" | "date">("today");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [adding, setAdding] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const today = a.tasks.day;
  const lists: Record<Tab, Task[]> = { today: a.tasks.today, upcoming: a.tasks.upcoming, done: a.tasks.done };
  const items = lists[tab];

  // Quand Atlas ajoute une tâche, on montre l'onglet où elle se trouve.
  useEffect(() => {
    if (!a.flashTask) return;
    if (a.tasks.today.some((t) => t.id === a.flashTask)) setTab("today");
    else if (a.tasks.upcoming.some((t) => t.id === a.flashTask)) setTab("upcoming");
  }, [a.flashTask, a.tasks]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || adding) return;
    setAdding(true);
    const due_date = when === "today" ? today || null : when === "tomorrow" ? (today ? addDays(today, 1) : "demain") : date || null;
    try {
      await api("/api/tasks", { method: "POST", json: { title: title.trim(), due_date, due_time: time || null } });
      setTitle("");
      setTime("");
      inputRef.current?.focus();
    } catch (err) {
      a.toast({ kind: "error", text: (err as Error).message });
    } finally {
      setAdding(false);
    }
  };

  return (
    <section className="panel-section card tasks" aria-labelledby="tasks-heading">
      <div className="section-head">
        <h2 id="tasks-heading">Tâches</h2>
        <div className="seg" role="tablist" aria-label="Filtrer les tâches">
          {([
            ["today", "Aujourd'hui", a.tasks.today.length],
            ["upcoming", "À venir", a.tasks.upcoming.length],
            ["done", "Faites", null],
          ] as [Tab, string, number | null][]).map(([id, label, n]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
              {label}
              {n ? <span className="count">{n}</span> : null}
            </button>
          ))}
        </div>
      </div>

      {tab !== "done" && (
        <form className="task-add" onSubmit={add}>
          <PlusIcon size={16} />
          <label className="sr-only" htmlFor="task-new">Nouvelle tâche</label>
          <input id="task-new" ref={inputRef} value={title} maxLength={200} placeholder="Ajouter une tâche…" onChange={(e) => setTitle(e.target.value)} />
          {title && (
            <div className="task-add-opts">
              <select value={when} onChange={(e) => setWhen(e.target.value as any)} aria-label="Quand">
                <option value="today">Aujourd'hui</option>
                <option value="tomorrow">Demain</option>
                <option value="date">Date…</option>
              </select>
              {when === "date" && <input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Date" />}
              <input type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Heure (facultative)" />
              <button className="btn btn-gold btn-sm" disabled={adding}>Ajouter</button>
            </div>
          )}
        </form>
      )}

      {items.length === 0 ? (
        <p className="empty">
          {tab === "today"
            ? "Rien pour aujourd'hui. Ajoute une tâche ou dis à Atlas : « ajoute une tâche… »."
            : tab === "upcoming" ? "Aucune tâche à venir." : "Aucune tâche terminée récemment."}
        </p>
      ) : (
        <ul className="task-list">
          {items.map((t) => (
            <TaskRow key={t.id} t={t} today={today} flash={a.flashTask === t.id} />
          ))}
        </ul>
      )}
    </section>
  );
}
