"""Appel du matin (et appels courts de l'après-midi et du soir) : planification et sonnerie.

* L'heure de coucher est notée quand l'utilisateur dit « bonne nuit » (interface, appel ou outil MCP).
  Couché avant l'heure limite (23:30 par défaut) → appel tôt (05:30), sinon → appel tard (08:00).
* La sonnerie passe par ntfy (appli Android gratuite) : un message de priorité maximale sur un sujet
  secret et aléatoire ; le toucher ouvre la page « Appel » d'Atlas, qui décroche toute seule.
* Une seule relance, 15 minutes plus tard, si l'appel n'a pas été décroché.
* Autour de l'heure d'un appel, Atlas demande à Windows de ne pas se rendormir.

Tout l'état vit dans les préférences (aucune migration) ; l'horloge et le réseau sont injectables
pour les tests.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sys
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("atlas.calls")

CALL_DEFAULTS: dict[str, Any] = {
    "calls_enabled": True,
    "call_bedtime_cutoff": "23:30",
    "call_early": "05:30",
    "call_late": "08:00",
    "call_afternoon_enabled": True,
    "call_afternoon": "14:00",
    "call_evening_enabled": True,
    "call_evening": "20:00",
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",          # généré au premier démarrage (secret, aléatoire)
    "call_public_url": "",     # adresse Tailscale HTTPS (ex. https://hp.tailnet.ts.net)
}
TIME_KEYS = ("call_bedtime_cutoff", "call_early", "call_late", "call_afternoon", "call_evening")
BOOL_KEYS = ("calls_enabled", "call_afternoon_enabled", "call_evening_enabled")
SLOTS = ("morning", "afternoon", "evening")
SLOT_LABEL = {"morning": "Appel du matin", "afternoon": "Point de l'après-midi", "evening": "Bilan du soir",
              "test": "Appel de test", "live": "Appel"}
RETRY_AFTER_S = 15 * 60
RING_WINDOW_S = 60 * 60       # au-delà d'1 h de retard (PC endormi…), on ne sonne plus pour ce créneau
AWAKE_BEFORE_S = 10 * 60
AWAKE_AFTER_S = 40 * 60
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
GOODNIGHT_RE = re.compile(r"\bbonne\s+nuit\b")


class CallError(ValueError):
    pass


def new_topic() -> str:
    return "atlas-" + secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")


def validate_setting(key: str, value: Any) -> Any:
    if key in TIME_KEYS:
        if not isinstance(value, str) or not TIME_RE.match(value):
            raise CallError("Heure invalide (HH:MM attendu).")
        return value
    if key in BOOL_KEYS:
        if not isinstance(value, bool):
            raise CallError("Valeur invalide.")
        return value
    if key == "ntfy_topic":
        if not isinstance(value, str) or not TOPIC_RE.match(value):
            raise CallError("Sujet ntfy invalide (16 à 64 lettres, chiffres, - ou _).")
        return value
    if key in ("ntfy_server", "call_public_url"):
        text = str(value or "").strip().rstrip("/")
        if text and not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", text):
            raise CallError("Adresse HTTPS attendue (ex. https://hp.tailnet.ts.net).")
        return text
    raise CallError(f"Réglage inconnu : {key}")


def settings_from(prefs: dict[str, Any]) -> dict[str, Any]:
    return {k: prefs.get(k, v) for k, v in CALL_DEFAULTS.items()}


def _at(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def morning_time(day: date, settings: dict[str, Any], bedtime_ts: float | None, tz: ZoneInfo) -> tuple[str, str]:
    """(HH:MM, raison). Coucher la veille avant l'heure limite → tôt ; sinon (ou inconnu) → tard."""
    if bedtime_ts:
        bed = datetime.fromtimestamp(bedtime_ts, tz)
        evening_start = _at(day - timedelta(days=1), "12:00", tz)
        cutoff = _at(day - timedelta(days=1), settings["call_bedtime_cutoff"], tz)
        if evening_start <= bed < _at(day, "06:00", tz):
            if bed < cutoff:
                return settings["call_early"], f"couché à {bed:%H:%M}, avant {settings['call_bedtime_cutoff']}"
            return settings["call_late"], f"couché à {bed:%H:%M}, après {settings['call_bedtime_cutoff']}"
    return settings["call_late"], "heure de coucher non notée"


def plan(day: date, prefs: dict[str, Any], tz: ZoneInfo) -> list[dict[str, Any]]:
    s = settings_from(prefs)
    if not s["calls_enabled"]:
        return []
    bedtime = (prefs.get("last_bedtime") or {}).get("ts")
    hhmm, why = morning_time(day, s, bedtime, tz)
    slots = [{"slot": "morning", "time": hhmm, "why": why}]
    if s["call_afternoon_enabled"]:
        slots.append({"slot": "afternoon", "time": s["call_afternoon"], "why": "réglage"})
    if s["call_evening_enabled"]:
        slots.append({"slot": "evening", "time": s["call_evening"], "why": "réglage"})
    for item in slots:
        item["at"] = _at(day, item["time"], tz).timestamp()
    return slots


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in text if not unicodedata.combining(c))


def is_goodnight(text: str) -> bool:
    return bool(GOODNIGHT_RE.search(normalize(text)))


class CallScheduler:
    """Décide quand sonner. ``send`` envoie la notification (remplacé dans les tests)."""

    def __init__(self, db: Any, timezone: Callable[[], str], *, send: Callable[[dict, dict], None] | None = None,
                 now: Callable[[], float] = time.time, on_change: Callable[[], Any] | None = None):
        self.db, self._tz, self.now = db, timezone, now
        self.send = send or send_ntfy
        self.on_change = on_change
        self._awake = False

    # -- état ----------------------------------------------------------------------
    def prefs(self) -> dict[str, Any]:
        return self.db.get_preferences()

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self._tz())

    def ensure_topic(self) -> str:
        topic = self.prefs().get("ntfy_topic")
        if not topic:
            topic = new_topic()
            self.db.set_preference("ntfy_topic", topic)
        return topic

    def _log(self) -> dict[str, Any]:
        log_ = self.prefs().get("call_log") or {}
        return log_ if isinstance(log_, dict) else {}

    def _save_log(self, log_: dict[str, Any]) -> None:
        keep = sorted(log_)[-7:]  # 7 derniers jours
        self.db.set_preference("call_log", {k: log_[k] for k in keep})

    def entry(self, day: str, slot: str) -> dict[str, Any]:
        return self._log().get(day, {}).get(slot, {})

    def _update(self, day: str, slot: str, **fields: Any) -> dict[str, Any]:
        log_ = self._log()
        item = log_.setdefault(day, {}).setdefault(slot, {})
        for key, value in fields.items():
            if key == "rang":
                item.setdefault("rang", []).append(value)
            else:
                item[key] = value
        self._save_log(log_)
        return item

    # -- actions --------------------------------------------------------------------
    def note_bedtime(self, ts: float | None = None) -> dict[str, Any]:
        ts = ts or self.now()
        tz = self.tz()
        bed = datetime.fromtimestamp(ts, tz)
        self.db.set_preference("last_bedtime", {"ts": ts, "local": bed.strftime("%Y-%m-%d %H:%M")})
        wake_day = bed.date() + timedelta(days=1) if bed.hour >= 12 else bed.date()
        nxt = next((p for p in plan(wake_day, self.prefs(), tz) if p["slot"] == "morning"), None)
        return {"coucher": bed.strftime("%H:%M"), "jour_appel": wake_day.isoformat(),
                "appel_du_matin": nxt["time"] if nxt else None, "raison": nxt["why"] if nxt else "appels désactivés"}

    def answered(self, slot: str) -> None:
        if slot in SLOTS:
            day = datetime.fromtimestamp(self.now(), self.tz()).date().isoformat()
            if self.entry(day, slot).get("rang"):
                self._update(day, slot, answered=self.now())

    def ring(self, slot: str, *, retry: bool = False) -> dict[str, Any]:
        s = settings_from(self.prefs())
        topic = self.ensure_topic()
        base = s["call_public_url"] or "http://localhost:8765"
        label = SLOT_LABEL.get(slot, "Appel")
        payload = {
            "topic": topic, "title": "Atlas t'appelle", "priority": 5, "tags": ["telephone_receiver"],
            "message": f"{label}{' (relance)' if retry else ''} — touche pour décrocher.",
            "click": f"{base}/appel?slot={slot}",
        }
        self.send(payload, s)
        return payload

    def status(self) -> dict[str, Any]:
        tz = self.tz()
        now = datetime.fromtimestamp(self.now(), tz)
        prefs = self.prefs()
        today = plan(now.date(), prefs, tz)
        tomorrow = plan(now.date() + timedelta(days=1), prefs, tz)
        for item in today:
            e = self.entry(now.date().isoformat(), item["slot"])
            item["sonneries"] = len(e.get("rang", []))
            item["decroche"] = bool(e.get("answered"))
        for item in today:
            item["quand"] = "aujourd'hui"
        for item in tomorrow:
            item["quand"] = "demain"
        upcoming = [p for p in today if p["at"] > now.timestamp()] + tomorrow
        return {"aujourdhui": today, "prochain": upcoming[0] if upcoming else None,
                "coucher": prefs.get("last_bedtime"), "reglages": settings_from(prefs)}

    # -- boucle ------------------------------------------------------------------------
    def tick(self) -> list[str]:
        """Appelée toutes les ~15 s. Renvoie les sonneries envoyées (pour les tests et le journal)."""
        tz = self.tz()
        now_ts = self.now()
        now = datetime.fromtimestamp(now_ts, tz)
        day = now.date().isoformat()
        sent: list[str] = []
        slots = plan(now.date(), self.prefs(), tz)
        for item in slots:
            e = self.entry(day, item["slot"])
            rang = e.get("rang", [])
            if e.get("answered"):
                continue
            if not rang and item["at"] <= now_ts < item["at"] + RING_WINDOW_S:
                self._ring_logged(day, item["slot"], retry=False)
                sent.append(item["slot"])
            elif len(rang) == 1 and now_ts >= rang[0] + RETRY_AFTER_S and now_ts < rang[0] + RETRY_AFTER_S + RING_WINDOW_S:
                self._ring_logged(day, item["slot"], retry=True)
                sent.append(item["slot"] + ":relance")
        around = slots + plan(now.date() + timedelta(days=1), self.prefs(), tz)
        self._keep_awake(any(p["at"] - AWAKE_BEFORE_S <= now_ts <= p["at"] + AWAKE_AFTER_S for p in around))
        return sent

    def _ring_logged(self, day: str, slot: str, *, retry: bool) -> None:
        # Noté AVANT l'envoi : en cas d'échec réseau on ne sonne pas en boucle (une tentative par étape).
        self._update(day, slot, rang=self.now())
        try:
            self.ring(slot, retry=retry)
        except Exception as exc:  # réseau coupé au réveil du PC…
            log.warning("Sonnerie %s impossible : %s", slot, exc)
            self._update(day, slot, error=str(exc)[:200])

    def _keep_awake(self, wanted: bool) -> None:
        if wanted == self._awake or sys.platform != "win32":
            self._awake = wanted
            return
        try:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if wanted else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self._awake = wanted
        except Exception:
            log.exception("Maintien en éveil impossible")


def send_ntfy(payload: dict[str, Any], settings: dict[str, Any], attempts: int = 3) -> None:
    """Publie sur ntfy (JSON). Réessaie quelques fois : le Wi-Fi met parfois du temps à revenir après la veille."""
    server = (settings.get("ntfy_server") or CALL_DEFAULTS["ntfy_server"]).rstrip("/")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(server + "/", data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    raise OSError(f"ntfy a répondu {resp.status}")
            return
        except Exception as exc:
            last = exc
            time.sleep(min(20, 5 * (i + 1)))
    raise OSError(f"ntfy injoignable : {last}")


def call_context(slot: str | None, start: bool, end: bool, settings: dict[str, Any] | None = None) -> list[str]:
    """Consignes ajoutées au contexte envoyé à Hermes pendant un appel."""
    if not slot:
        return []
    parts = ["MODE APPEL (conversation téléphonique, lue à voix haute) : réponds en 2 à 3 phrases maximum, "
             "une question à la fois, sans liste ni mise en forme ; le détail va à l'écran via tes outils mcp_atlas_*."]
    if start and slot == "morning":
        parts.append("C'est l'APPEL DU MATIN, tu viens d'appeler Abdeladim et il décroche : salue-le en une phrase, puis "
                     "suis ta compétence suivi-quotidien, section Matin, dans cet ordre : (1) rappelle le cockpit en UNE "
                     "phrase avec les chiffres de mcp_atlas_cockpit ; (2) propose UNE action du jour qui rapporte et, "
                     "une fois validée, écris-la avec mcp_atlas_action_du_jour_definir ; (3) fixe avec lui ses 3 priorités "
                     "puis ÉCRIS-LES toi-même avec mcp_atlas_priorite_ajouter. Une étape par réponse.")
    elif start and slot == "afternoon":
        parts.append("C'est le POINT DE L'APRÈS-MIDI (appel court) : demande où en est l'action du jour qui rapporte "
                     "et ce qui bloque (compétence suivi-quotidien, Après-midi).")
    elif start and slot == "evening":
        parts.append("C'est le BILAN DU SOIR (appel court) : fais le bilan avec la compétence suivi-quotidien, "
                     "section Soir, en commençant par l'argent encaissé aujourd'hui.")
    elif start and slot == "test":
        parts.append("C'est un APPEL DE TEST : dis en une phrase que la ligne fonctionne et demande s'il t'entend bien.")
    if end:
        parts.append("Il vient de dire au revoir (il raccroche) : si sa phrase validait quelque chose, fais-le d'abord "
                     "avec tes outils, puis conclus en UNE courte phrase.")
    return parts
