/**
 * Contrôleur de l'interface : état partagé, temps réel, envoi des messages, voix.
 * Aucune action ici ne déclenche d'appel au modèle sans un geste explicite de l'utilisateur.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, deviceId, localPref, randomId, setAuthLostHandler, setLocalPref } from "./api";
import { unlockAudio } from "./audio/context";
import { VoicePlayer } from "./audio/player";
import { RecorderError } from "./audio/recorder";
import { SpeechQueue } from "./audio/speech";
import { HandsFreeMic } from "./audio/vad";
import { setClockOffset } from "./time";
import type {
  ActivityEvent,
  Bootstrap,
  CallState,
  ClockInfo,
  Task,
  Conversation,
  HermesStatus,
  Message,
  Note,
  OrbState,
  Preferences,
  Priority,
  ServerState,
} from "./types";
import { RealtimeClient, type WsStatus } from "./ws";

export interface Toast {
  id: number;
  kind: "info" | "error" | "warning" | "success";
  text: string;
  action?: { label: string; run: () => void };
}

interface PendingSend {
  requestId: string;
  conversationId: string;
  content: string;
  kind: "text" | "voice";
  noteIds: string[];
  wantTts: boolean;
}

export interface PriorityDraft {
  title: string;
  description: string;
}

const IDLE_CALL: CallState = { active: false, phase: "listening", startedAt: 0, muted: false, userCaption: "", atlasCaption: "" };

async function transcribe(wav: Blob): Promise<string> {
  const resp = await fetch("/api/voice/transcribe", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "audio/wav" },
    body: wav,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data && data.detail;
    throw new Error((detail && (detail.message || detail)) || `Erreur ${resp.status}`);
  }
  return String(data.text || "").trim();
}

function reportClient(event: string) {
  api("/api/activity/client", { method: "POST", json: { event } }).catch(() => undefined);
}

function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((t: Omit<Toast, "id">, ttl = 6000) => {
    const id = ++seq.current;
    setToasts((list) => [...list.slice(-3), { ...t, id }]);
    if (ttl) window.setTimeout(() => setToasts((list) => list.filter((x) => x.id !== id)), ttl);
    return id;
  }, []);
  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((x) => x.id !== id)), []);
  return { toasts, push, dismiss };
}

function systemReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function useAtlasController() {
  const [phase, setPhase] = useState<"loading" | "login" | "ready" | "unreachable">("loading");
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [hermes, setHermes] = useState<HermesStatus | null>(null);
  const [serverState, setServerState] = useState<ServerState>({ state: "idle", label: "Disponible" });
  const [wsStatus, setWsStatus] = useState<WsStatus>("connecting");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(() => localPref<string | null>("conversation", null));
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const messagesRef = useRef<Message[]>([]);
  const [priorities, setPriorities] = useState<Priority[]>([]);
  const [prioritiesDay, setPrioritiesDay] = useState<string>("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [notesVersion, setNotesVersion] = useState(0);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [draft, setDraftState] = useState<string>("");
  const [attachedNotes, setAttachedNotes] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [uncertain, setUncertain] = useState<PendingSend | null>(null);
  const [localVoice, setLocalVoice] = useState<"none" | "listening" | "transcribing" | "speaking">("none");
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [blockedPlayback, setBlockedPlayback] = useState<string | null>(null);
  const [autoplay, setAutoplayState] = useState<boolean>(() => localPref<boolean>("autoplay", true));
  const [volume, setVolumeState] = useState<number>(() => localPref<number>("volume", 0.9));
  const [micDevice, setMicDeviceState] = useState<string>(() => localPref<string>("micDevice", ""));
  const [priorityDraft, setPriorityDraft] = useState<PriorityDraft | null>(null);
  const [clockOffset, setClockOffsetState] = useState<number | null>(null);
  const [tasks, setTasks] = useState<{ today: Task[]; upcoming: Task[]; done: Task[]; day: string }>({
    today: [], upcoming: [], done: [], day: "",
  });
  const [financeVersion, setFinanceVersion] = useState(0);
  const [flashTask, setFlashTask] = useState<string | null>(null);
  const [call, setCall] = useState<CallState>(IDLE_CALL);
  const [bargeIn, setBargeInState] = useState<boolean>(() => localPref<boolean>("bargeIn", true));
  const { toasts, push: toast, dismiss } = useToasts();

  const me = useMemo(() => deviceId(), []);
  const dictation = useRef<HandsFreeMic | null>(null);
  const callMic = useRef<HandsFreeMic | null>(null);
  const callRef = useRef(call);
  callRef.current = call;
  const bargeInRef = useRef(bargeIn);
  bargeInRef.current = bargeIn;
  const player = useRef(new VoicePlayer());
  const speechRef = useRef<SpeechQueue | null>(null);
  const sendRef = useRef<(content: string, opts?: { kind?: "text" | "voice" }) => Promise<boolean>>(async () => false);
  const rt = useRef<RealtimeClient | null>(null);
  messagesRef.current = messages;
  const activeConvRef = useRef(activeConvId);
  activeConvRef.current = activeConvId;
  const autoplayRef = useRef(autoplay);
  autoplayRef.current = autoplay;
  const volumeRef = useRef(volume);
  volumeRef.current = volume;
  const wasConnected = useRef(false);

  // -- brouillon conservé par conversation (stockage local du navigateur) ------------
  const setDraft = useCallback((text: string) => {
    setDraftState(text);
    if (activeConvRef.current) setLocalPref("draft." + activeConvRef.current, text);
  }, []);
  useEffect(() => {
    setDraftState(activeConvId ? localPref<string>("draft." + activeConvId, "") : "");
    setAttachedNotes([]);
  }, [activeConvId]);

  // -- chargements ---------------------------------------------------------------------
  const loadConversations = useCallback(async () => {
    const list = await api<Conversation[]>("/api/conversations");
    setConversations(list);
    return list;
  }, []);

  const loadMessages = useCallback(async (convId: string) => {
    setMessagesLoading(true);
    try {
      const data = await api<{ messages: Message[] }>(`/api/conversations/${convId}/messages`);
      if (activeConvRef.current === convId) setMessages(data.messages);
      return data.messages;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setActiveConvId(null);
        setLocalPref("conversation", null);
      }
      throw e;
    } finally {
      setMessagesLoading(false);
    }
  }, []);

  const loadPriorities = useCallback(async () => {
    const data = await api<{ day: string; items: Priority[] }>("/api/priorities");
    setPriorities(data.items);
    setPrioritiesDay(data.day);
  }, []);

  const loadNotes = useCallback(async () => {
    setNotes(await api<Note[]>("/api/notes"));
    setNotesVersion((v) => v + 1);
  }, []);

  const loadTasks = useCallback(async () => {
    const [today, upcoming, done] = await Promise.all([
      api<{ today: string; items: Task[] }>("/api/tasks?scope=today"),
      api<{ items: Task[] }>("/api/tasks?scope=upcoming"),
      api<{ items: Task[] }>("/api/tasks?scope=done"),
    ]);
    setTasks({ today: today.items, upcoming: upcoming.items, done: done.items.slice(0, 30), day: today.today });
  }, []);

  const loadActivity = useCallback(async () => {
    setActivity(await api<ActivityEvent[]>("/api/activity?limit=300"));
  }, []);

  // Heure : décalage du fuseau calculé par le PC (base de fuseaux à jour), pas par le navigateur.
  const applyClock = useCallback((c: ClockInfo | undefined | null) => {
    if (!c) return;
    setClockOffset(c.offset_min, c.tz);
    setClockOffsetState(c.offset_min);
  }, []);
  const loadClock = useCallback(async () => {
    try {
      applyClock(await api<ClockInfo>("/api/clock"));
    } catch {
      /* on garde le dernier décalage connu */
    }
  }, [applyClock]);
  useEffect(() => {
    const id = window.setInterval(() => loadClock(), 10 * 60 * 1000);
    return () => window.clearInterval(id);
  }, [loadClock]);

  const resync = useCallback(async () => {
    const jobs: Promise<unknown>[] = [loadConversations(), loadPriorities(), loadNotes(), loadActivity(), loadTasks(), loadClock()];
    if (activeConvRef.current) jobs.push(loadMessages(activeConvRef.current).catch(() => undefined));
    await Promise.all(jobs);
  }, [loadConversations, loadPriorities, loadNotes, loadActivity, loadMessages, loadTasks, loadClock]);

  // -- voix : lecture au fil de l'eau (uniquement sur l'appareil demandeur) ----------------
  const setCallPhase = useCallback((phase: CallState["phase"]) => {
    setCall((c) => (c.active ? { ...c, phase: c.muted && phase === "listening" ? "muted" : phase } : c));
  }, []);

  /** Micro de l'appel : écoute, pause, ou interruption possible pendant qu'Atlas parle. */
  const resumeCallListening = useCallback(() => {
    const c = callRef.current;
    if (!c.active || !callMic.current) return;
    callMic.current.setMode(c.muted ? "paused" : "listening");
    setCallPhase("listening");
  }, [setCallPhase]);

  if (!speechRef.current) {
    speechRef.current = new SpeechQueue(player.current, {
      fetchAudio: async (messageId, text) => {
        const blob = await api<Blob>("/api/voice/speak", { method: "POST", json: { message_id: messageId, text } });
        return { data: await new Response(blob).arrayBuffer(), mime: blob.type || "audio/mpeg" };
      },
      volume: () => volumeRef.current,
      onStart: (messageId) => {
        setSpeakingId(messageId);
        setLocalVoice("speaking");
        reportClient("playback_started");
        if (callRef.current.active && callMic.current) {
          callMic.current.setMode(callRef.current.muted ? "paused" : bargeInRef.current ? "bargein" : "paused");
          setCallPhase("speaking");
        }
      },
      onIdle: (_messageId, how) => {
        setSpeakingId(null);
        setLocalVoice((v) => (v === "speaking" ? "none" : v));
        if (how === "finished") reportClient("playback_finished");
        if (how === "stopped") reportClient("playback_stopped");
        if (callRef.current.active && callRef.current.phase !== "hearing") resumeCallListening();
      },
      onError: (message) =>
        toast({ kind: "warning", text: `Voix indisponible : ${message} La réponse écrite reste affichée.` }),
      onBlocked: (messageId) => {
        setBlockedPlayback(messageId);
        reportClient("playback_blocked");
      },
    });
  }
  const speech = speechRef.current;

  const stopSpeaking = useCallback(() => {
    speech.stop();
    setLocalVoice((v) => (v === "speaking" ? "none" : v));
    setSpeakingId(null);
  }, [speech]);

  /** Lecture d'une réponse déjà terminée (bouton « Écouter »). */
  const speak = useCallback(
    (messageId: string, content?: string) => {
      unlockAudio();
      setBlockedPlayback(null);
      const text = content !== undefined ? content : (messagesRef.current.find((m) => m.id === messageId) || { content: "" }).content;
      if (!text.trim()) return;
      speech.begin(messageId, text);
      speech.finish(text);
    },
    [speech],
  );

  // -- temps réel -----------------------------------------------------------------------
  const onRealtime = useCallback(
    (msg: any) => {
      switch (msg.type) {
        case "hello":
          setServerState(msg.state);
          setHermes(msg.hermes);
          if (wasConnected.current) resync().catch(() => undefined);
          wasConnected.current = true;
          break;
        case "atlas.state":
          setServerState(msg);
          break;
        case "hermes.status":
          setHermes(msg.status);
          break;
        case "message.created": {
          const m: Message = msg.message;
          if (m.conversation_id === activeConvRef.current)
            setMessages((list) => (list.some((x) => x.id === m.id) ? list : [...list, m]));
          // La voix n'est jouée QUE sur l'appareil qui a fait la demande, dès la première phrase.
          if (m.role === "assistant" && m.origin_device === me && m.meta.want_tts &&
              (autoplayRef.current || callRef.current.active)) {
            setBlockedPlayback(null);
            speech.begin(m.id);
            if (callRef.current.active) setCall((c) => ({ ...c, atlasCaption: "" }));
          }
          if (m.role === "user" && m.origin_device === me && callRef.current.active)
            setCall((c) => ({ ...c, userCaption: m.content }));
          break;
        }
        case "message.delta":
          if (msg.conversation_id === activeConvRef.current)
            setMessages((list) =>
              list.map((m) => (m.id === msg.message_id ? { ...m, content: m.content + msg.delta } : m)),
            );
          if (speech.messageId === msg.message_id) {
            speech.feed(msg.delta);
            if (callRef.current.active) setCall((c) => ({ ...c, atlasCaption: (c.atlasCaption + msg.delta).slice(-600) }));
          }
          break;
        case "message.completed": {
          const m: Message = msg.message;
          if (m.conversation_id === activeConvRef.current)
            setMessages((list) => (list.some((x) => x.id === m.id) ? list.map((x) => (x.id === m.id ? m : x)) : [...list, m]));
          if (speech.messageId === m.id) {
            if (m.status === "complete" && m.content.trim()) speech.finish(m.content);
            else speech.stop(false);
          }
          if (callRef.current.active && m.role === "assistant" && m.origin_device === me) {
            setCall((c) => ({ ...c, atlasCaption: m.content.slice(-600) }));
            if (m.status !== "complete" || !speech.active) resumeCallListening();
          }
          break;
        }
        case "finance.changed":
          setFinanceVersion((v) => v + 1);
          break;
        case "tasks.changed":
          loadTasks().catch(() => undefined);
          if (msg.by === "atlas") {
            const verbs: Record<string, string> = { added: "a ajouté", updated: "a modifié", completed: "a terminé", reopened: "a rouvert", deleted: "a supprimé" };
            toast({ kind: "success", text: `Atlas ${verbs[msg.action] || "a modifié"} « ${msg.task.title} »` });
            if (msg.action !== "deleted") {
              setFlashTask(msg.task.id);
              window.setTimeout(() => setFlashTask((id) => (id === msg.task.id ? null : id)), 4000);
            }
          }
          break;
        case "activity":
          setActivity((list) => [...list.slice(-299), msg.event]);
          break;
        case "conversations.changed":
          loadConversations().catch(() => undefined);
          break;
        case "priorities.changed":
          loadPriorities().catch(() => undefined);
          if (msg.by === "atlas") toast({ kind: "success", text: "Atlas a mis à jour tes priorités." });
          break;
        case "notes.changed":
          if (msg.origin !== me) loadNotes().catch(() => undefined);
          break;
        case "preferences.changed":
          setPrefs(msg.preferences);
          loadClock();
          break;
      }
    },
    [me, resync, loadConversations, loadPriorities, loadNotes, loadTasks, speech, toast, resumeCallListening, loadClock],
  );

  // -- démarrage ------------------------------------------------------------------------
  const start = useCallback(async () => {
    setPhase("loading");
    try {
      const pub = await api<{ authenticated: boolean }>("/api/public/status");
      if (!pub.authenticated) {
        setPhase("login");
        return;
      }
      const b = await api<Bootstrap>("/api/bootstrap");
      setBoot(b);
      setPrefs(b.preferences);
      setHermes(b.hermes);
      setServerState(b.state);
      applyClock(b.clock);
      if (localPref<boolean | null>("autoplay", null) === null) setAutoplayState(b.preferences.autoplay_voice);
      const list = await loadConversations();
      let conv = activeConvRef.current && list.find((c) => c.id === activeConvRef.current) ? activeConvRef.current : null;
      if (!conv && list.length) conv = list[0].id;
      if (conv) {
        setActiveConvId(conv);
        activeConvRef.current = conv;
        await loadMessages(conv).catch(() => undefined);
      }
      await Promise.all([loadPriorities(), loadNotes(), loadActivity(), loadTasks()]);
      setPhase("ready");
      if (!rt.current) {
        rt.current = new RealtimeClient(onRealtime, setWsStatus);
        rt.current.start();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setPhase("login");
      else setPhase("unreachable");
    }
  }, [loadConversations, loadMessages, loadPriorities, loadNotes, loadActivity, loadTasks, onRealtime, applyClock]);

  useEffect(() => {
    setAuthLostHandler(() => {
      rt.current?.stop();
      rt.current = null;
      setPhase("login");
    });
    start();
    return () => {
      rt.current?.stop();
      rt.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Le gestionnaire temps réel doit voir les dernières fonctions.
  useEffect(() => {
    if (rt.current) (rt.current as any).onMessage = onRealtime;
  }, [onRealtime]);

  // -- conversations ---------------------------------------------------------------------
  const openConversation = useCallback(
    async (convId: string) => {
      setActiveConvId(convId);
      activeConvRef.current = convId;
      setLocalPref("conversation", convId);
      setMessages([]);
      await loadMessages(convId).catch(() => undefined);
    },
    [loadMessages],
  );

  const newConversation = useCallback(async () => {
    const conv = await api<Conversation>("/api/conversations", { method: "POST" });
    await loadConversations();
    await openConversation(conv.id);
    return conv;
  }, [loadConversations, openConversation]);

  const renameConversation = useCallback(async (convId: string, title: string) => {
    await api(`/api/conversations/${convId}`, { method: "PATCH", json: { title } });
  }, []);

  const deleteConversation = useCallback(
    async (convId: string) => {
      await api(`/api/conversations/${convId}`, { method: "DELETE" });
      const list = await loadConversations();
      if (activeConvRef.current === convId) {
        if (list.length) await openConversation(list[0].id);
        else {
          setActiveConvId(null);
          setMessages([]);
        }
      }
    },
    [loadConversations, openConversation],
  );

  // -- envoi ---------------------------------------------------------------------------------
  const busy = serverState.state !== "idle";

  const submit = useCallback(
    async (pending: PendingSend) => {
      setSending(true);
      try {
        await api(`/api/conversations/${pending.conversationId}/messages`, {
          method: "POST",
          json: {
            request_id: pending.requestId,
            content: pending.content,
            device_id: me,
            kind: pending.kind,
            want_tts: pending.wantTts,
            note_ids: pending.noteIds,
          },
        });
        setUncertain(null);
        if (pending.kind === "text" && activeConvRef.current === pending.conversationId) {
          setDraft("");
          setAttachedNotes([]);
        }
        return true;
      } catch (e) {
        const err = e as ApiError;
        if (err.status === 0) {
          // On ne sait pas si le serveur a reçu la demande : on ne renvoie rien automatiquement.
          setUncertain(pending);
          if (pending.kind === "voice") setDraft(pending.content);
          toast({ kind: "error", text: "Connexion perdue pendant l'envoi. Ton texte est conservé. « Réessayer » ne pourra pas l'exécuter deux fois." }, 0);
        } else if (err.status === 409) {
          if (pending.kind === "voice") setDraft(pending.content);
          toast({ kind: "warning", text: "Atlas est déjà occupé. Ton message est conservé : envoie-le quand il aura fini." });
        } else {
          if (pending.kind === "voice") setDraft(pending.content);
          toast({ kind: "error", text: err.message || "Envoi impossible." }, 9000);
        }
        return false;
      } finally {
        setSending(false);
      }
    },
    [me, setDraft, toast],
  );

  const send = useCallback(
    async (content: string, opts: { kind?: "text" | "voice" } = {}) => {
      const text = content.trim();
      if (!text || sending) return false;
      if (busy) {
        toast({ kind: "warning", text: "Atlas est occupé. Attends la fin ou arrête la tâche en cours." });
        return false;
      }
      let convId = activeConvRef.current;
      if (!convId) convId = (await newConversation()).id;
      const kind = opts.kind || "text";
      return submit({
        requestId: randomId("req"),
        conversationId: convId!,
        content: text,
        kind,
        noteIds: attachedNotes,
        wantTts: kind === "voice" && (autoplayRef.current || callRef.current.active),
      });
    },
    [sending, busy, toast, newConversation, submit, attachedNotes],
  );
  sendRef.current = send;

  const retryUncertain = useCallback(() => {
    if (uncertain) submit(uncertain); // même request_id : le serveur ignore un doublon
  }, [uncertain, submit]);

  const stopTask = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; reason?: string }>("/api/turn/stop", { method: "POST" });
      if (!r.ok) toast({ kind: "warning", text: r.reason === "not_started" ? "La tâche démarre, réessaie dans un instant." : "Arrêt impossible avec cette version d'Hermes." });
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    }
  }, [toast]);

  const answerApproval = useCallback(
    async (choice: "once" | "session" | "deny") => {
      try {
        await api("/api/turn/approval", { method: "POST", json: { choice } });
      } catch (e) {
        toast({ kind: "error", text: (e as Error).message });
      }
    },
    [toast],
  );

  // -- voix : micro ---------------------------------------------------------------------------
  const micError = useCallback(
    (e: unknown) => {
      const err = e as RecorderError;
      if (err.kind === "denied") reportClient("mic_denied");
      if (err.kind === "nodevice") reportClient("mic_unavailable");
      toast({ kind: "error", text: err.message || "Micro indisponible." }, 10000);
    },
    [toast],
  );

  /** Dictée ponctuelle : s'arrête toute seule quand tu te tais. */
  const startListening = useCallback(async () => {
    unlockAudio();
    if (dictation.current || callRef.current.active) return;
    if (busy) {
      toast({ kind: "warning", text: "Atlas est occupé : attends la fin de la réponse en cours." });
      return;
    }
    stopSpeaking();
    let mic: HandsFreeMic;
    try {
      mic = await HandsFreeMic.open({
        deviceId: micDevice || undefined,
        silenceMs: 1000,
        maxSeconds: 120,
        onUtterance: async (wav) => {
          mic.close();
          dictation.current = null;
          setLocalVoice("transcribing");
          try {
            const text = await transcribe(wav);
            if (!text) toast({ kind: "info", text: "Je n'ai rien entendu. Réessaie en parlant un peu plus près du micro." });
            else if (prefs && prefs.voice_confirm_before_send) {
              setDraft(text);
              toast({ kind: "info", text: "Texte reconnu placé dans la zone de saisie : vérifie-le puis envoie." });
            } else await sendRef.current(text, { kind: "voice" });
          } catch (e) {
            toast({ kind: "error", text: `Transcription impossible : ${(e as Error).message}` }, 9000);
          } finally {
            setLocalVoice((v) => (v === "transcribing" ? "none" : v));
          }
        },
      });
    } catch (e) {
      micError(e);
      return;
    }
    dictation.current = mic;
    setLocalVoice("listening");
  }, [busy, micDevice, prefs, setDraft, stopSpeaking, toast, micError]);

  /** « J'ai fini » : envoie tout de suite ce qui a été dit (facultatif, le silence suffit). */
  const finishListening = useCallback(() => {
    if (dictation.current && !dictation.current.flush()) {
      dictation.current.close();
      dictation.current = null;
      setLocalVoice((v) => (v === "listening" ? "none" : v));
    }
  }, []);

  const cancelListening = useCallback(() => {
    if (dictation.current) dictation.current.close();
    dictation.current = null;
    setLocalVoice((v) => (v === "listening" ? "none" : v));
  }, []);

  // -- appel en direct ------------------------------------------------------------------------
  const onCallUtterance = useCallback(
    async (wav: Blob) => {
      const mic = callMic.current;
      if (!mic || !callRef.current.active) return;
      mic.setMode("paused");
      setCallPhase("transcribing");
      let text = "";
      try {
        text = await transcribe(wav);
      } catch (e) {
        toast({ kind: "error", text: `Transcription impossible : ${(e as Error).message}` }, 8000);
      }
      if (!callRef.current.active) return;
      if (!text) {
        resumeCallListening();
        return;
      }
      setCall((c) => ({ ...c, userCaption: text, atlasCaption: "" }));
      setCallPhase("waiting");
      const ok = await sendRef.current(text, { kind: "voice" });
      if (!ok) resumeCallListening();
    },
    [toast, setCallPhase, resumeCallListening],
  );

  const startCall = useCallback(async () => {
    unlockAudio();
    if (callRef.current.active) return;
    cancelListening();
    stopSpeaking();
    if (boot && !boot.voice.stt.available) {
      toast({ kind: "error", text: "La transcription vocale n'est pas disponible (Réglages > Voix)." }, 8000);
      return;
    }
    setCall({ ...IDLE_CALL, active: true, phase: "connecting", startedAt: Date.now() });
    try {
      callMic.current = await HandsFreeMic.open({
        deviceId: micDevice || undefined,
        silenceMs: 850,
        maxSeconds: 60,
        onSpeechStart: () => setCallPhase("hearing"),
        onBargeIn: () => {
          // Tu parles pendant qu'Atlas parle : on coupe sa voix et on t'écoute.
          speech.stop(false);
          setLocalVoice((v) => (v === "speaking" ? "none" : v));
          setSpeakingId(null);
          reportClient("playback_stopped");
        },
        onUtterance: (wav) => onCallUtterance(wav),
      });
    } catch (e) {
      setCall(IDLE_CALL);
      micError(e);
      return;
    }
    if (serverState.state !== "idle") {
      callMic.current.setMode("paused");
      setCallPhase("waiting");
    } else setCallPhase("listening");
  }, [boot, micDevice, serverState.state, cancelListening, stopSpeaking, speech, setCallPhase, onCallUtterance, micError, toast]);

  const endCall = useCallback(() => {
    if (callMic.current) callMic.current.close();
    callMic.current = null;
    speech.stop(false);
    setLocalVoice("none");
    setSpeakingId(null);
    setCall(IDLE_CALL);
  }, [speech]);

  const toggleMute = useCallback(() => {
    setCall((c) => {
      const muted = !c.muted;
      if (callMic.current) {
        if (muted) callMic.current.setMode("paused");
        else if (c.phase === "muted" || c.phase === "listening") callMic.current.setMode("listening");
      }
      const phase = muted && c.phase === "listening" ? "muted" : !muted && c.phase === "muted" ? "listening" : c.phase;
      return { ...c, muted, phase };
    });
  }, []);

  // Fin d'une réponse sans voix (erreur, annulation) pendant un appel : on réécoute.
  useEffect(() => {
    if (call.active && call.phase === "waiting" && serverState.state === "idle" && !speech.active) {
      const t = window.setTimeout(() => {
        if (callRef.current.phase === "waiting" && !speech.active) resumeCallListening();
      }, 1500);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [call.active, call.phase, serverState.state, speech, resumeCallListening]);

  useEffect(() => () => {
    if (callMic.current) callMic.current.close();
    if (dictation.current) dictation.current.close();
  }, []);

  const audioLevel = useCallback((): number | null => {
    if (player.current.playing) return player.current.analysed ? player.current.level() : null;
    if (callMic.current) return callMic.current.level();
    if (dictation.current) return dictation.current.level();
    return 0;
  }, []);

  // -- préférences ---------------------------------------------------------------------------
  const setAutoplay = useCallback((v: boolean) => {
    setAutoplayState(v);
    setLocalPref("autoplay", v);
  }, []);
  const setVolume = useCallback((v: number) => {
    setVolumeState(v);
    setLocalPref("volume", v);
    player.current.setVolume(v);
  }, []);
  const setBargeIn = useCallback((v: boolean) => {
    setBargeInState(v);
    setLocalPref("bargeIn", v);
  }, []);
  const setMicDevice = useCallback((v: string) => {
    setMicDeviceState(v);
    setLocalPref("micDevice", v);
  }, []);
  const updatePrefs = useCallback(async (patch: Partial<Preferences>) => {
    const next = await api<Preferences>("/api/preferences", { method: "PATCH", json: patch });
    setPrefs(next);
    return next;
  }, []);

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    rt.current?.stop();
    rt.current = null;
    setPhase("login");
  }, []);

  // Après une reconnexion : si la demande « incertaine » a bien été reçue, on l'oublie.
  useEffect(() => {
    if (uncertain && messages.some((m) => m.request_id === uncertain.requestId)) {
      setUncertain(null);
      if (activeConvRef.current === uncertain.conversationId) setDraft("");
      toast({ kind: "success", text: "Ton dernier message avait bien été reçu par Atlas." });
    }
  }, [messages, uncertain, setDraft, toast]);

  // -- état de l'orbe ---------------------------------------------------------------------------
  const reduceMotion = prefs ? (prefs.reduce_motion === "system" ? systemReducedMotion() : prefs.reduce_motion === "on") : false;
  const offline = phase === "ready" && wsStatus !== "open";
  let orbState: OrbState = "idle";
  let orbLabel = "Disponible";
  const callPhaseLabel: Record<string, [OrbState, string]> = {
    connecting: ["listening", "Connexion du micro…"],
    listening: ["listening", "Je t'écoute"],
    hearing: ["listening", "Je t'écoute…"],
    muted: ["idle", "Micro coupé"],
    transcribing: ["transcribing", "Je transcris…"],
  };
  if (offline) {
    orbState = "offline";
    orbLabel = wsStatus === "connecting" ? "Connexion…" : "Hors ligne";
  } else if (call.active && callPhaseLabel[call.phase]) {
    [orbState, orbLabel] = callPhaseLabel[call.phase];
  } else if (localVoice === "listening") {
    orbState = "listening";
    orbLabel = "À l'écoute";
  } else if (localVoice === "transcribing") {
    orbState = "transcribing";
    orbLabel = "Je transcris…";
  } else if (localVoice === "speaking") {
    orbState = "speaking";
    orbLabel = "Je parle";
  } else if (serverState.state === "waiting_approval") {
    orbState = "waiting_approval";
    orbLabel = "J'attends ton accord";
  } else if (serverState.state === "acting") {
    orbState = "acting";
    orbLabel = serverState.label;
  } else if (serverState.state === "thinking") {
    orbState = "thinking";
    orbLabel = serverState.label || "Je réfléchis…";
  } else if (hermes && !hermes.authenticated) {
    orbState = "error";
    orbLabel = "Hermes indisponible";
  }

  return {
    phase, start, boot, prefs, hermes, serverState, wsStatus, offline, conversations, activeConvId, messages,
    messagesLoading, priorities, prioritiesDay, notes, notesVersion, activity, draft, setDraft, attachedNotes,
    setAttachedNotes, sending, uncertain, retryUncertain, dismissUncertain: () => setUncertain(null), busy, send,
    stopTask, answerApproval, openConversation, newConversation, renameConversation, deleteConversation,
    loadPriorities, loadNotes, setNotes, loadActivity, localVoice, speakingId, speak, stopSpeaking,
    blockedPlayback, startListening, finishListening, cancelListening, audioLevel, autoplay,
    tasks, loadTasks, financeVersion, flashTask, call, startCall, endCall, toggleMute, bargeIn, setBargeIn,
    setAutoplay, volume, setVolume, micDevice, setMicDevice, updatePrefs, reduceMotion, orbState, orbLabel,
    toasts, toast, dismiss, logout, me, priorityDraft, setPriorityDraft, clockOffset,
    reconnect: () => rt.current?.reconnectNow(),
  };
}

export type Atlas = ReturnType<typeof useAtlasController>;
const Ctx = createContext<Atlas | null>(null);

export function AtlasProvider({ children }: { children: ReactNode }) {
  const atlas = useAtlasController();
  return <Ctx.Provider value={atlas}>{children}</Ctx.Provider>;
}

export function useAtlas(): Atlas {
  const v = useContext(Ctx);
  if (!v) throw new Error("AtlasProvider manquant");
  return v;
}
