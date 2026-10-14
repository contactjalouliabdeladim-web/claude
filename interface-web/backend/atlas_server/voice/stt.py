"""Transcription vocale.

* ``local_whisper`` : faster-whisper sur le PC Windows. L'audio ne quitte pas
  le PC. Le modèle est chargé à la première dictée (quelques secondes) puis
  déchargé après 10 minutes sans usage pour libérer la mémoire.
  Installation optionnelle : ``requirements-voix-locale.txt``.
* ``hermes_dashboard`` : relais vers ``POST /api/audio/transcribe`` du tableau
  de bord Hermes (réutilise la configuration STT d'Hermes, qui peut être
  locale ou distante selon ce qui y est configuré).
* ``off`` : pas de dictée.

Le navigateur envoie un WAV PCM 16 bits mono (16 kHz) : format universel,
produit même par les anciens Safari sans ``MediaRecorder``.
"""

from __future__ import annotations

import asyncio
import base64
import io
import threading
import time
import wave

import httpx

MAX_SECONDS = 120
INITIAL_PROMPT_FR = (
    "Abdeladim, Atlas, Scaleo Agency, Massar, Rome Arab Tours, Kick-Off TV, Ecologia, "
    "dirhams, DH, bénéfice net, marketing digital, publicité."
)


class STTError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def decode_wav(data: bytes):
    """WAV PCM16 → (numpy float32 mono 16 kHz, durée en secondes)."""
    import numpy as np

    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise STTError("format", "Format audio non reconnu (WAV PCM attendu).") from exc
    if width != 2:
        raise STTError("format", "Format audio non pris en charge (PCM 16 bits attendu).")
    samples = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and len(samples):
        target = int(len(samples) * 16000 / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, target), np.arange(len(samples)), samples)
        samples = samples.astype("float32")
    return samples, len(samples) / 16000


def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate() or 1)
    except (wave.Error, EOFError) as exc:
        raise STTError("format", "Format audio non reconnu (WAV PCM attendu).") from exc


class STTService:
    IDLE_UNLOAD_S = 600

    def __init__(self, provider: str, whisper_model: str = "small", dashboard_url: str = "",
                 dashboard_token: str = "", models_dir: str | None = None):
        self.provider = provider
        self.whisper_model = whisper_model
        self.dashboard_url = dashboard_url.rstrip("/")
        self.dashboard_token = dashboard_token
        self.models_dir = models_dir
        self._model = None
        self._model_lock = threading.Lock()
        self._last_used = 0.0
        self._busy = asyncio.Lock()

    def local_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def describe(self) -> dict:
        info = {"provider": self.provider, "available": self.provider != "off"}
        if self.provider == "local_whisper":
            info["available"] = self.local_available()
            info["where"] = "Sur ce PC (faster-whisper, modèle " + self.whisper_model + ")"
            info["loaded"] = self._model is not None
            if not info["available"]:
                info["hint"] = "Module faster-whisper non installé (voir Installer-Atlas.ps1, option voix locale)."
        elif self.provider == "hermes_dashboard":
            info["where"] = "Tableau de bord Hermes (selon sa configuration STT)"
        else:
            info["where"] = "Désactivée"
        return info

    async def transcribe(self, wav_bytes: bytes) -> dict:
        duration = wav_duration(wav_bytes)
        if duration < 0.3:
            raise STTError("empty", "Enregistrement trop court.")
        if duration > MAX_SECONDS + 2:
            raise STTError("too_long", f"Enregistrement trop long (max {MAX_SECONDS} s).")
        if self.provider == "off":
            raise STTError("disabled", "La transcription est désactivée.")
        if self.provider == "local_whisper":
            if not self.local_available():
                raise STTError("missing", "La transcription locale n'est pas installée sur le PC.")
            async with self._busy:  # une transcription à la fois : le portable a 2 cœurs
                text = await asyncio.to_thread(self._transcribe_local, wav_bytes)
        elif self.provider == "hermes_dashboard":
            text = await self._transcribe_dashboard(wav_bytes)
        else:
            raise STTError("config", f"Fournisseur de transcription inconnu : {self.provider}")
        return {"text": text.strip(), "duration_s": round(duration, 1), "provider": self.provider}

    def _load(self):
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.whisper_model, device="cpu", compute_type="int8",
                                           download_root=self.models_dir)
            return self._model

    def warm(self) -> None:
        """Précharge le modèle local en arrière-plan pour éviter le délai du premier tour."""
        if self.provider != "local_whisper" or not self.local_available():
            return
        try:
            self._load()
            self._last_used = time.monotonic()
        except Exception:
            # Le serveur reste disponible ; la dictée affichera l'erreur si le modèle ne charge pas.
            return

    def _transcribe_local(self, wav_bytes: bytes) -> str:
        samples, _ = decode_wav(wav_bytes)
        try:
            model = self._load()
        except Exception as exc:
            raise STTError("model", f"Impossible de charger le modèle Whisper ({type(exc).__name__}). "
                                    "Le premier usage nécessite Internet pour le télécharger.") from exc
        self._last_used = time.monotonic()
        segments, _info = model.transcribe(samples, language="fr", beam_size=2, vad_filter=True,
                                          initial_prompt=INITIAL_PROMPT_FR)
        return " ".join(seg.text.strip() for seg in segments)

    def maybe_unload(self) -> None:
        if self._model is not None and time.monotonic() - self._last_used > self.IDLE_UNLOAD_S:
            with self._model_lock:
                self._model = None

    async def _transcribe_dashboard(self, wav_bytes: bytes) -> str:
        payload = {"data_url": "data:audio/wav;base64," + base64.b64encode(wav_bytes).decode(),
                   "mime_type": "audio/wav"}
        headers = {"X-Hermes-Session-Token": self.dashboard_token} if self.dashboard_token else {}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self.dashboard_url}/api/audio/transcribe", json=payload,
                                         headers=headers)
        except httpx.HTTPError as exc:
            raise STTError("service", "Le tableau de bord Hermes ne répond pas.") from exc
        if resp.status_code != 200:
            raise STTError("service", f"Le tableau de bord Hermes a refusé la transcription ({resp.status_code}).")
        return str(resp.json().get("transcript") or "")
