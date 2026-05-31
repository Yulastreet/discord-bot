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
import re
from typing import Optional

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
