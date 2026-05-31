"""Text-to-speech via Microsoft Edge TTS (gratuit, voix neurales).

Genere un MP3 a partir d'un texte, en utilisant les voix neurales de Microsoft
disponibles via le service Edge Read Aloud. Aucune cle API requise.

Doc librairie : https://github.com/rany2/edge-tts
Voix dispo : `python -m edge_tts --list-voices`

Voix FR recommandees :
- fr-FR-DeniseNeural   (femme, naturelle, par defaut)
- fr-FR-HenriNeural    (homme, posee)
- fr-FR-EloiseNeural   (jeune femme, energique)
- fr-FR-VivienneMultilingualNeural (multi-langue)
"""
from __future__ import annotations

import io
import os
import re
from typing import Optional

import aiohttp

try:
    import edge_tts
    _HAS_EDGE_TTS = True
except Exception:
    edge_tts = None
    _HAS_EDGE_TTS = False


# Texte trop long = audio trop long. Cap raisonnable pour Discord.
MAX_TTS_CHARS = 600


def _sanitize_for_tts(text: str) -> str:
    """Nettoie le texte avant TTS : retire markdown, mentions Discord, emojis lourds,
    code blocks, URLs (sinon le moteur lit toute l'URL caractere par caractere).
    """
    if not text:
        return ""
    s = text
    # Code blocks ``` ... ```
    s = re.sub(r"```[\s\S]*?```", " ", s)
    # Inline code `...`
    s = re.sub(r"`([^`]*)`", r"\1", s)
    # Mentions <@123>, <@!123>, <#123>, <@&123>
    s = re.sub(r"<@!?\d+>", "", s)
    s = re.sub(r"<#\d+>", "", s)
    s = re.sub(r"<@&\d+>", "", s)
    # Emojis custom <:name:id>
    s = re.sub(r"<a?:\w+:\d+>", "", s)
    # URLs https://... -> "lien"
    s = re.sub(r"https?://\S+", "lien", s)
    # Markdown bold/italic/strike
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"_([^_]+)_", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    # Compresse espaces
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_TTS_CHARS]


async def synthesize_voice(text: str,
                            voice: str = "fr-FR-DeniseNeural",
                            rate: str = "+0%",
                            pitch: str = "+0Hz") -> bytes:
    """Genere les bytes MP3 d'une synthese vocale.

    Leve RuntimeError si edge-tts n'est pas installe ou si la generation echoue.
    """
    if not _HAS_EDGE_TTS:
        raise RuntimeError("edge-tts non installe (pip install edge-tts).")

    clean = _sanitize_for_tts(text)
    if not clean:
        raise RuntimeError("Texte vide apres nettoyage TTS.")

    communicate = edge_tts.Communicate(text=clean, voice=voice, rate=rate, pitch=pitch)
    buf = io.BytesIO()
    try:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf.write(chunk["data"])
    except Exception as e:
        raise RuntimeError(f"Echec TTS edge-tts: {type(e).__name__}: {e}") from e

    data = buf.getvalue()
    if not data:
        raise RuntimeError("TTS a produit un audio vide.")
    return data


def is_available() -> bool:
    """True si edge-tts est installe (utile pour le dashboard owner)."""
    return _HAS_EDGE_TTS


# ===================== ELEVENLABS =====================

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


def get_elevenlabs_api_key() -> Optional[str]:
    return (os.getenv("ELEVENLABS_API_KEY", "") or "").strip() or None


def elevenlabs_available() -> bool:
    return bool(get_elevenlabs_api_key())


async def synthesize_voice_elevenlabs(text: str,
                                       voice_id: str = "XB0fDUnXU5powFXDhCwa",
                                       model_id: str = "eleven_multilingual_v2",
                                       stability: float = 0.5,
                                       similarity_boost: float = 0.75,
                                       style: float = 0.0,
                                       use_speaker_boost: bool = True,
                                       timeout_sec: float = 25.0) -> bytes:
    """Genere MP3 via API ElevenLabs.

    Free tier : 10 000 caracteres/mois. Voice IDs premade FR-friendly via le
    modele multilingual : Charlotte (XB0fDUnXU5powFXDhCwa), Charlie
    (IKne3meq5aSn9XLyUdCD), Sarah (EXAVITQu4vr4xnSDxMaL), Brian
    (nPczCjzI2devNBz1zQrb), Lily (pFZP5JQG7iQjIQuC4Bku).

    Leve RuntimeError avec message clair en cas d'erreur (quota, auth, etc.) ;
    l'appelant peut alors retomber sur edge-tts.
    """
    key = get_elevenlabs_api_key()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY non configuree dans .env")

    clean = _sanitize_for_tts(text)
    if not clean:
        raise RuntimeError("Texte vide apres nettoyage TTS.")

    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": clean,
        "model_id": model_id,
        "voice_settings": {
            "stability": float(stability),
            "similarity_boost": float(similarity_boost),
            "style": float(style),
            "use_speaker_boost": bool(use_speaker_boost),
        },
    }

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as r:
            if r.status == 200:
                data = await r.read()
                if not data:
                    raise RuntimeError("ElevenLabs a renvoye un audio vide")
                return data
            # Erreur : extrait le message
            try:
                err = await r.json()
                msg = (err.get("detail") or err.get("message") or str(err))[:300]
            except Exception:
                msg = (await r.text())[:300]
            if r.status == 401:
                raise RuntimeError(f"ElevenLabs auth refusee (cle invalide ?): {msg}")
            if r.status == 429:
                raise RuntimeError(f"ElevenLabs rate-limit / quota epuise: {msg}")
            raise RuntimeError(f"ElevenLabs {r.status}: {msg}")


async def get_elevenlabs_quota() -> Optional[dict]:
    """Retourne {used, limit, remaining} ou None si erreur. Utile pour dashboard."""
    key = get_elevenlabs_api_key()
    if not key:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{ELEVENLABS_BASE}/user/subscription",
                                    headers={"xi-api-key": key}) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                used = int(data.get("character_count") or 0)
                limit = int(data.get("character_limit") or 0)
                return {"used": used, "limit": limit,
                        "remaining": max(0, limit - used)}
    except Exception:
        return None


# ===================== DISPATCH =====================

async def synthesize(text: str, provider: str = "edge", **kwargs) -> tuple[bytes, str]:
    """Genere un MP3 via le provider choisi, avec fallback edge si elevenlabs fail.

    Retourne (audio_bytes, provider_used_str). provider_used peut differer du
    demande si fallback a ete declenche.
    """
    provider = (provider or "edge").lower()
    if provider == "elevenlabs":
        try:
            audio = await synthesize_voice_elevenlabs(
                text,
                voice_id=kwargs.get("elevenlabs_voice_id", "XB0fDUnXU5powFXDhCwa"),
                model_id=kwargs.get("elevenlabs_model", "eleven_multilingual_v2"),
            )
            return audio, "elevenlabs"
        except Exception as e:
            print(f"[tts] elevenlabs fail -> fallback edge: {e!r}")
            # Fallback edge ci-dessous
    audio = await synthesize_voice(text, voice=kwargs.get("edge_voice", "fr-FR-DeniseNeural"))
    return audio, "edge"
