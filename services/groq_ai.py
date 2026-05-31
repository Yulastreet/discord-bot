"""Client minimal pour l'API Groq (compatible OpenAI chat completions).

Doc : https://console.groq.com/docs/quickstart
Modèles dispo : llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b-32768,
gemma2-9b-it, etc.
"""
from __future__ import annotations

import os
import aiohttp
from typing import Optional


GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


def get_groq_api_key() -> Optional[str]:
    return (os.getenv("GROQ_API_KEY", "") or "").strip() or None


async def groq_chat(prompt: str, *,
                    system_prompt: str = "",
                    model: str = "llama-3.3-70b-versatile",
                    max_tokens: int = 400,
                    temperature: float = 0.7,
                    history: Optional[list] = None,
                    timeout_sec: float = 20.0) -> str:
    """Appelle Groq et retourne le texte de la réponse.

    `history` : liste de messages precedents [{"role": "user"/"assistant", "content": ...}]
    pour donner du contexte conversationnel au modele. Lève RuntimeError si la
    clé manque ou si l'API renvoie une erreur.
    """
    key = get_groq_api_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY non configurée dans .env")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        for h in history:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
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
                raise RuntimeError(f"Réponse Groq inattendue : {str(data)[:200]}")
            usage = data.get("usage") or {}
            return {
                "text":              txt,
                "prompt_tokens":     int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens":      int(usage.get("total_tokens") or 0),
                "model":             data.get("model") or model,
            }
