export type MessageStatus = "complete" | "streaming" | "error" | "interrupted" | "cancelled";

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  request_id: string | null;
  run_id: string | null;
  origin_device: string | null;
  meta: {
    kind?: "text" | "voice";
    want_tts?: boolean;
    error_kind?: string;
    error?: string;
    usage?: Record<string, number>;
    runtime?: { provider?: string; model?: string };
    notes?: string[];
  };
  created_at: number;
  updated_at: number;
}

export interface Conversation {
  id: string;
  title: string;
  hermes_session_id: string | null;
  created_at: number;
  updated_at: number;
  message_count?: number;
}

export interface Priority {
  id: string;
  title: string;
  description: string;
  status: "todo" | "doing" | "done";
  day: string;
  due: string | null;
  position: number;
  created_at: number;
  updated_at: number;
}

export interface Note {
  id: string;
  content: string;
  created_at: number;
  updated_at: number;
}

export interface FinanceTransaction {
  id: string;
  kind: "income" | "expense";
  amount_cents: number;
  currency: "MAD" | "EUR";
  date: string;
  client: string;
  category: string;
  note: string;
  created_at: number;
}

export interface FinanceAmount {
  income_cents: number;
  expense_cents: number;
  net_cents: number;
}

export interface FinanceOverview {
  date: string;
  month_start: string;
  today: Record<"MAD" | "EUR", FinanceAmount>;
  month: Record<"MAD" | "EUR", FinanceAmount>;
  all_time: Record<"MAD" | "EUR", FinanceAmount>;
  transactions: FinanceTransaction[];
}

export interface ActivityEvent {
  id: number;
  ts: number;
  category: string;
  label: string;
  status: string;
  duration_ms: number | null;
  detail: string | null;
  run_id: string | null;
  conversation_id: string | null;
}

export interface HermesStatus {
  reachable: boolean;
  authenticated: boolean;
  mode: "sessions" | "chat_completions" | "unavailable" | "demo";
  error_kind: string | null;
  error: string | null;
  model_alias: string | null;
  features: Record<string, boolean>;
  checked_at: number;
}

export interface Approval {
  request_id: string | null;
  command: string | null;
  description: string | null;
  choices: string[];
}

export interface ServerState {
  type?: string;
  state: "idle" | "thinking" | "acting" | "waiting_approval" | "error";
  label: string;
  conversation_id?: string;
  message_id?: string;
  run_id?: string | null;
  approval?: Approval | null;
  started_at?: number;
  can_stop?: boolean;
  partial_content?: string;
}

export interface Preferences {
  timezone: string;
  autoplay_voice: boolean;
  reduce_motion: "system" | "on" | "off";
  voice_confirm_before_send: boolean;
  cockpit_goal_mad: number;
  cockpit_start: string;
  cockpit_end: string;
  cockpit_eur_mad: number;
  cockpit_prior_mad: number;
}

export interface MoneyAction {
  jour: string;
  action: string;
  pourquoi: string;
  par: string;
  le: number;
}

export interface Cockpit {
  objectif_mad: number;
  debut: string;
  fin: string;
  aujourdhui: string;
  taux_eur_mad: number;
  acquis_avant_suivi_mad: number;
  net_mad: number;
  net_eur: number;
  cumul_net_mad: number;
  reste_mad: number;
  pourcentage: number;
  jours_total: number;
  jours_ecoules: number;
  jours_restants: number;
  rythme_necessaire_semaine_mad: number | null;
  rythme_reel_semaine_mad: number | null;
  attendu_a_date_mad: number;
  ecart_mad: number;
  ecart_jours: number;
  etat: "dans_les_temps" | "leger_retard" | "gros_retard" | "atteint";
  resume: string;
  action_du_jour: MoneyAction | null;
}

export interface VoiceInfo {
  stt: { provider: string; available: boolean; where?: string; hint?: string; loaded?: boolean };
  tts: { provider: string; available: boolean; where?: string; voice?: string | null };
  hermes_config: { tts_provider: string | null; edge_voice: string | null; stt_provider: string | null };
}

export interface Bootstrap {
  version: string;
  demo: boolean;
  device: string;
  preferences: Preferences;
  hermes: HermesStatus;
  state: ServerState;
  last_runtime: { provider?: string; model?: string; at?: number } | null;
  network: { lan: boolean; https: boolean; port: number; urls: string[]; local_url: string; ca_available: boolean };
  voice: VoiceInfo;
  schema_version: number;
  data_dir: string;
  mcp?: { configured: boolean; port: number; last_seen: number | null };
  clock?: ClockInfo;
}

/** États visuels de l'orbe : ceux du serveur + ceux propres à cet appareil (micro, lecture). */
export type OrbState =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "acting"
  | "waiting_approval"
  | "speaking"
  | "error"
  | "offline";

export interface Task {
  id: string;
  title: string;
  notes: string;
  due_date: string | null;
  due_time: string | null;
  status: "todo" | "done";
  source: "user" | "atlas";
  created_at: number;
  updated_at: number;
  completed_at: number | null;
}

export type CallPhase = "connecting" | "listening" | "hearing" | "transcribing" | "waiting" | "speaking" | "muted";

export interface CallState {
  active: boolean;
  phase: CallPhase;
  startedAt: number;
  muted: boolean;
  userCaption: string;
  atlasCaption: string;
}


export interface ClockInfo {
  tz: string;
  offset_min: number;
  server_utc_ms: number;
}