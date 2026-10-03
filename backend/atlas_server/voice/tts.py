"""Synthèse vocale.

* ``edge`` : Microsoft Edge TTS via la bibliothèque ``edge-tts`` — le même moteur
  que celui configuré dans Hermes. **Service distant de Microsoft** : le texte
  de la réponse est envoyé à Microsoft pour être lu. Gratuit, sans clé, mais
  non garanti (service non officiel côté API).
* ``hermes_dashboard`` : relais vers ``POST /api/audio/speak`` du tableau de
  bord Hermes (réutilise exactement la configuration TTS d'Hermes).
* ``off`` : pas de synthèse ; la réponse écrite reste toujours disponible.
"""

from __future__ import annotations

import base64
import re

import httpx

DEFAULT_VOICE = "fr-FR-HenriNeural"
MAX_SPEECH_CHARS = 2500


class TTSError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def text_for_speech(text: str) -> tuple[str, bool]:
    """Nettoie le Markdown pour la lecture. Retourne (texte, tronqué)."""
    text = re.sub(r"```.*?```", " (bloc de code affiché à l'écran) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " (lien) ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>#|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPEECH_CHARS:
        cut = text[:MAX_SPEECH_CHARS]
        cut = cut[: max(cut.rfind(". "), MAX_SPEECH_CHARS // 2) + 1]
        return cut + " La suite est à l'écran.", True
    return text, False


class TTSService:
    def __init__(self, provider: str, voice: str, dashboard_url: str = "", dashboard_token: str = ""):
        self.provider = provider
        self.voice = voice or DEFAULT_VOICE
        self.dashboard_url = dashboard_url.rstrip("/")
        self.dashboard_token = dashboard_token

    def describe(self) -> dict:
        remote = {"edge": "Service Microsoft Edge TTS (en ligne)",
                  "hermes_dashboard": "Tableau de bord Hermes (selon sa configuration)",
                  "off": "Désactivée"}
        return {"provider": self.provider, "voice": self.voice if self.provider == "edge" else None,
                "where": remote.get(self.provider, self.provider), "available": self.provider != "off"}

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        speech, _ = text_for_speech(text)
        if not speech:
            raise TTSError("empty", "Rien à lire.")
        if self.provider == "off":
            raise TTSError("disabled", "La synthèse vocale est désactivée.")
        if self.provider == "edge":
            return await self._edge(speech), "audio/mpeg"
        if self.provider == "hermes_dashboard":
            return await self._dashboard(speech)
        raise TTSError("config", f"Fournisseur de synthèse inconnu : {self.provider}")

    async def _edge(self, text: str) -> bytes:
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError("missing", "Le module edge-tts n'est pas installé.") from exc
        chunks: list[bytes] = []
        try:
            communicate = edge_tts.Communicate(text, self.voice)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
        except Exception as exc:  # réseau, voix inconnue, service indisponible
            raise TTSError("service", f"Edge TTS a échoué ({type(exc).__name__}). "
                                      "Vérifie la connexion Internet du PC.") from exc
        if not chunks:
            raise TTSError("service", "Edge TTS n'a renvoyé aucun audio.")
        return b"".join(chunks)

    async def _dashboard(self, text: str) -> tuple[bytes, str]:
        headers = {"X-Hermes-Session-Token": self.dashboard_token} if self.dashboard_token else {}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{self.dashboard_url}/api/audio/speak", json={"text": text},
                                         headers=headers)
        except httpx.HTTPError as exc:
            raise TTSError("service", "Le tableau de bord Hermes ne répond pas.") from exc
        if resp.status_code != 200:
            raise TTSError("service", f"Le tableau de bord Hermes a refusé ({resp.status_code}).")
        data = resp.json()
        url = data.get("data_url") or ""
        if "," not in url:
            raise TTSError("service", "Réponse audio invalide.")
        header, encoded = url.split(",", 1)
        return base64.b64decode(encoded), data.get("mime_type") or header[5:].split(";")[0]
