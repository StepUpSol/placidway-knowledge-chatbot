"""Thin wrapper around the OpenAI client so the provider can be swapped in one place."""
import json
import os
from functools import lru_cache

from openai import OpenAI

from app import config


@lru_cache(maxsize=1)
def client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(timeout=40, max_retries=2)


def embed_texts(texts, batch=96):
    out = []
    for i in range(0, len(texts), batch):
        resp = client().embeddings.create(
            model=config.EMBED_MODEL, input=texts[i:i + batch], dimensions=config.EMBED_DIMENSIONS)
        out.extend(d.embedding for d in resp.data)
    return out


def chat_json(system: str, user: str, max_tokens=700) -> dict:
    """Call the chat model and parse a JSON object reply."""
    resp = client().chat.completions.create(
        model=config.CHAT_MODEL,
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content or "{}")
