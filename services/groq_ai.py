"""Client minimal pour l'API Groq (compatible OpenAI chat completions).

Doc : https://console.groq.com/docs/quickstart
Available models: llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b-32768,
gemma2-9b-it, etc.
"""
from __future__ import annotations

import os
import aiohttp
from typing import Optional


GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


def get_groq_api_key() -> Optional[str]:
    return (os.getenv("GROQ_API_KEY", "") or "").strip() or None


async def groq_chat(prompt, *,
                    system_prompt: str = "",
                    model: str = "llama-3.3-70b-versatile",
                    max_tokens: int = 400,
                    temperature: float = 0.7,
                    history: Optional[list] = None,
                    image_urls: Optional[list] = None,
                    timeout_sec: float = 20.0) -> str:
    """Appelle Groq et retourne {text, *_tokens, model}.

    `prompt` : texte du message courant. Peut etre str ou directement une liste
        au format content multimodal (text + image_url).
    `history` : liste [{"role": "user"/"assistant", "content": ...}]
    `image_urls` : si fourni, on switch en mode vision et on ajoute ces URLs
        d'images au message courant. Necessite un modele vision (ex:
        meta-llama/llama-4-scout-17b-16e-instruct).

    Leve RuntimeError si la cle manque ou si l'API renvoie une erreur.
    """
    key = get_groq_api_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured in .env")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        for h in history:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    # Construit le message user courant : texte simple OU content multimodal
    if image_urls:
        parts = [{"type": "text", "text": prompt if isinstance(prompt, str) else str(prompt)}]
        for url in image_urls[:5]:   # limite raisonnable (Groq accepte ~5 images)
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        messages.append({"role": "user", "content": parts})
    else:
        messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(GROQ_BASE_URL, json=payload, headers=headers) as r:
            data = await r.json()
            if r.status != 200:
                err = (data or {}).get("error", {})
                msg = err.get("message") or str(data)[:200]
                raise RuntimeError(f"Groq {r.status}: {msg}")
            try:
                txt = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise RuntimeError(f"Unexpected Groq response: {str(data)[:200]}")
            usage = data.get("usage") or {}
            return {
                "text":              txt,
                "prompt_tokens":     int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens":      int(usage.get("total_tokens") or 0),
                "model":             data.get("model") or model,
            }
