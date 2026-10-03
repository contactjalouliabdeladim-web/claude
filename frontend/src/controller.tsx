/**
 * Contrôleur de l'interface : état partagé, temps réel, envoi des messages, voix.
 * Aucune action ici ne déclenche d'appel au modèle sans un geste explicite de l'utilisateur.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, deviceId, localPref, randomId, setAuthLostHandler, setLocalPref } from "./api";
import { unlockAudio } from "./audio/context";
import { PlaybackBlocked, VoicePlayer } from "./audio/player";
import { RecorderError, startRecording, type RecorderHandle } from "./audio/recorder";
import type {
  ActivityEvent,
  Bootstrap,
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

const MAX_RECORD_S = 120;

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
  const [recordSeconds, setRecordSeconds] = useState(0);
  const { toasts, push: toast, dismiss } = useToasts();

  const me = useMemo(() => deviceId(), []);
  const recorder = useRef<RecorderHandle | null>(null);
  const player = useRef(new VoicePlayer());
  const rt = useRef<RealtimeClient | null>(null);
  const activeConvRef = useRef(activeConvId);
  activeConvRef.current = activeConvId;
  const autoplayRef = useRef(autoplay);
  autoplayRef.current = autoplay;
  const volumeRef = useRef(volume);
  volumeRef.current = volume;
  const wasConnected = useRef(false);
  const recordTimer = useRef<number | null>(null);

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

  const loadActivity = useCallback(async () => {
    setActivity(await api<ActivityEvent[]>("/api/activity?limit=300"));
  }, []);

  const resync = useCallback(async () => {
    const tasks: Promise<unknown>[] = [loadConversations(), loadPriorities(), loadNotes(), loadActivity()];
    if (activeConvRef.current) tasks.push(loadMessages(activeConvRef.current).catch(() => undefined));
    await Promise.all(tasks);
  }, [loadConversations, loadPriorities, loadNotes, loadActivity, loadMessages]);

  // -- voix : lecture -------------------------------------------------------------------
  const stopSpeaking = useCallback(() => {
    if (player.current.playing) {
      player.current.stop();
      api("/api/activity/client", { method: "POST", json: { event: "playback_stopped" } }).catch(() => undefined);
    }
    setLocalVoice((v) => (v === "speaking" ? "none" : v));
    setSpeakingId(null);
  }, []);

  const speak = useCallback(
    async (messageId: string) => {
      setBlockedPlayback(null);
      player.current.stop();
      setSpeakingId(messageId);
      setLocalVoice("speaking");
      let audio: Blob;
      try {
        audio = await api<Blob>("/api/voice/speak", { method: "POST", json: { message_id: messageId } });
      } catch (e) {
        setLocalVoice((v) => (v === "speaking" ? "none" : v));
        setSpeakingId(null);
        toast({ kind: "warning", text: `Voix indisponible : ${(e as Error).message} La réponse écrite reste affichée.` });
        return;
      }
      try {
        const buf = await new Response(audio).arrayBuffer();
        api("/api/activity/client", { method: "POST", json: { event: "playback_started" } }).catch(() => undefined);
        const how = await player.current.play(buf, audio.type || "audio/mpeg", volumeRef.current);
        if (how === "ended")
          api("/api/activity/client", { method: "POST", json: { event: "playback_finished" } }).catch(() => undefined);
      } catch (e) {
        if (e instanceof PlaybackBlocked) {
          setBlockedPlayback(messageId);
          api("/api/activity/client", { method: "POST", json: { event: "playback_blocked" } }).catch(() => undefined);
        } else toast({ kind: "warning", text: "Lecture audio impossible sur ce navigateur." });
      } finally {
        setLocalVoice((v) => (v === "speaking" ? "none" : v));
        setSpeakingId((id) => (id === messageId ? null : id));
      }
    },
    [toast],
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
        case "message.created":
          if (msg.message.conversation_id === activeConvRef.current)
            setMessages((list) => (list.some((m) => m.id === msg.message.id) ? list : [...list, msg.message]));
          break;
        case "message.delta":
          if (msg.conversation_id === activeConvRef.current)
            setMessages((list) =>
              list.map((m) => (m.id === msg.message_id ? { ...m, content: m.content + msg.delta } : m)),
            );
          break;
        case "message.completed": {
          const m: Message = msg.message;
          if (m.conversation_id === activeConvRef.current)
            setMessages((list) => (list.some((x) => x.id === m.id) ? list.map((x) => (x.id === m.id ? m : x)) : [...list, m]));
          // La voix n'est jouée QUE sur l'appareil qui a fait la demande.
          if (m.status === "complete" && m.origin_device === me && m.meta.want_tts && autoplayRef.current && m.content.trim())
            speak(m.id);
          break;
        }
        case "activity":
          setActivity((list) => [...list.slice(-299), msg.event]);
          break;
        case "conversations.changed":
          loadConversations().catch(() => undefined);
          break;
        case "priorities.changed":
          loadPriorities().catch(() => undefined);
          break;
        case "notes.changed":
          if (msg.origin !== me) loadNotes().catch(() => undefined);
          break;
        case "preferences.changed":
          setPrefs(msg.preferences);
          break;
      }
    },
    [me, resync, loadConversations, loadPriorities, loadNotes, speak],
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
      if (localPref<boolean | null>("autoplay", null) === null) setAutoplayState(b.preferences.autoplay_voice);
      const list = await loadConversations();
      let conv = activeConvRef.current && list.find((c) => c.id === activeConvRef.current) ? activeConvRef.current : null;
      if (!conv && list.length) conv = list[0].id;
      if (conv) {
        setActiveConvId(conv);
        activeConvRef.current = conv;
        await loadMessages(conv).catch(() => undefined);
      }
      await Promise.all([loadPriorities(), loadNotes(), loadActivity()]);
      setPhase("ready");
      if (!rt.current) {
        rt.current = new RealtimeClient(onRealtime, setWsStatus);
        rt.current.start();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setPhase("login");
      else setPhase("unreachable");
    }
  }, [loadConversations, loadMessages, loadPriorities, loadNotes, loadActivity, onRealtime]);

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
        wantTts: kind === "voice" && autoplayRef.current,
      });
    },
    [sending, busy, toast, newConversation, submit, attachedNotes],
  );

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

  // -- voix : micro ------------------------------------------------------------------------
  const stopRecordTimer = () => {
    if (recordTimer.current) window.clearInterval(recordTimer.current);
    recordTimer.current = null;
  };

  const finishListening = useCallback(async () => {
    const rec = recorder.current;
    if (!rec) return;
    recorder.current = null;
    stopRecordTimer();
    setLocalVoice("transcribing");
    try {
      const wav = await rec.stop();
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
      const text: string = (data.text || "").trim();
      if (!text) {
        toast({ kind: "info", text: "Je n'ai rien entendu. Réessaie en parlant un peu plus près du micro." });
        return;
      }
      if (prefs && prefs.voice_confirm_before_send) {
        setDraft(text);
        toast({ kind: "info", text: "Texte reconnu placé dans la zone de saisie : vérifie-le puis envoie." });
      } else {
        await send(text, { kind: "voice" });
      }
    } catch (e) {
      toast({ kind: "error", text: `Transcription impossible : ${(e as Error).message}` }, 9000);
    } finally {
      setLocalVoice((v) => (v === "transcribing" ? "none" : v));
    }
  }, [prefs, send, setDraft, toast]);

  const startListening = useCallback(async () => {
    unlockAudio();
    if (recorder.current) return;
    if (busy) {
      toast({ kind: "warning", text: "Atlas est occupé : attends la fin de la réponse en cours." });
      return;
    }
    stopSpeaking();
    try {
      recorder.current = await startRecording(micDevice || undefined);
    } catch (e) {
      const err = e as RecorderError;
      if (err.kind === "denied") api("/api/activity/client", { method: "POST", json: { event: "mic_denied" } }).catch(() => undefined);
      if (err.kind === "nodevice") api("/api/activity/client", { method: "POST", json: { event: "mic_unavailable" } }).catch(() => undefined);
      toast({ kind: "error", text: err.message || "Micro indisponible." }, 10000);
      return;
    }
    setLocalVoice("listening");
    setRecordSeconds(0);
    recordTimer.current = window.setInterval(() => {
      const s = recorder.current ? recorder.current.seconds() : 0;
      setRecordSeconds(Math.floor(s));
      if (s >= MAX_RECORD_S) finishListening();
    }, 250);
  }, [busy, micDevice, stopSpeaking, toast, finishListening]);

  const cancelListening = useCallback(() => {
    if (recorder.current) recorder.current.cancel();
    recorder.current = null;
    stopRecordTimer();
    setLocalVoice((v) => (v === "listening" ? "none" : v));
  }, []);

  const audioLevel = useCallback((): number | null => {
    if (recorder.current) return recorder.current.level();
    if (player.current.playing) return player.current.analysed ? player.current.level() : null;
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
  if (offline) {
    orbState = "offline";
    orbLabel = wsStatus === "connecting" ? "Connexion…" : "Hors ligne";
  } else if (localVoice === "listening") {
    orbState = "listening";
    orbLabel = "À l'écoute";
  } else if (localVoice === "transcribing") {
    orbState = "transcribing";
    orbLabel = "Je transcris…";
  } else if (serverState.state === "waiting_approval") {
    orbState = "waiting_approval";
    orbLabel = "J'attends ton accord";
  } else if (serverState.state === "acting") {
    orbState = "acting";
    orbLabel = serverState.label;
  } else if (serverState.state === "thinking") {
    orbState = "thinking";
    orbLabel = serverState.label || "Je réfléchis…";
  } else if (localVoice === "speaking") {
    orbState = "speaking";
    orbLabel = "Je parle";
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
    blockedPlayback, startListening, finishListening, cancelListening, recordSeconds, audioLevel, autoplay,
    setAutoplay, volume, setVolume, micDevice, setMicDevice, updatePrefs, reduceMotion, orbState, orbLabel,
    toasts, toast, dismiss, logout, me, priorityDraft, setPriorityDraft,
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
