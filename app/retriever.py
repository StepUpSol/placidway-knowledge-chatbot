"""Hybrid retrieval over the chunked pages: embedding similarity plus a small keyword boost."""
import glob
import gzip
import json
import math
import os
import re
import threading

from app import config
from app.llm import embed_texts

_STOP = set("""a an the and or of to in on for with is are was were be by at from as it this that these those
what how much does do can i you me my your we our about which who when where why than then cost price""".split())

_lock = threading.Lock()
_kb = None


def _tokens(text):
    """Lower-cased word stems. A 5-letter prefix is a crude but effective stemmer here:
    located/location -> locat, included/includes -> inclu."""
    return {t[:5] for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 2}


def load_kb(force=False):
    """Prefer a knowledge base refreshed at runtime (/tmp), else the one shipped in the repo."""
    global _kb
    with _lock:
        if _kb is not None and not force:
            return _kb
        if os.path.exists(config.RUNTIME_KB_PATH):
            with open(config.RUNTIME_KB_PATH, encoding="utf-8") as f:
                kb = json.load(f)
        else:
            kb = None
            for path in sorted(glob.glob(os.path.join(config.DATA_DIR, "kb-part*.json.gz"))):
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    part = json.load(f)
                if kb is None:
                    kb = part
                else:
                    kb["chunks"].extend(part["chunks"])
        if any("embedding" not in c for c in kb["chunks"]):
            # First request on a fresh server: embed all chunks once (a single batched API call) and cache.
            vecs = embed_texts([f"{c['page_title']} | {c['heading']}\n{c['text']}" for c in kb["chunks"]])
            for c, v in zip(kb["chunks"], vecs):
                c["embedding"] = v
            try:
                with open(config.RUNTIME_KB_PATH, "w", encoding="utf-8") as f:
                    json.dump(kb, f, ensure_ascii=False)
            except OSError:
                pass
        df = {}
        for c in kb["chunks"]:
            c["_tokens"] = _tokens(f"{c['heading']} {c['text']}")
            c["_head"] = _tokens(c["heading"])
            for t in c["_tokens"]:
                df[t] = df.get(t, 0) + 1
            norm = math.sqrt(sum(x * x for x in c["embedding"])) or 1.0
            c["_vec"] = [x / norm for x in c["embedding"]]
            # "Listing:" cards advertise other clinics; useful, but rarely the page's main topic.
            listing_lines = sum(1 for ln in c["text"].split("\n") if ln.strip().startswith("Listing:"))
            c["_weight"] = 0.9 if listing_lines >= 2 else 1.0
        n = len(kb["chunks"])
        kb["_idf"] = {t: math.log(1 + n / d) for t, d in df.items()}
        _kb = kb
        return _kb


def set_kb(kb):
    global _kb
    with _lock:
        _kb = None
    with open(config.RUNTIME_KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False)
    return load_kb(force=True)


def search(query, top_k=config.TOP_K):
    kb = load_kb()
    qv = embed_texts([query])[0]
    qn = math.sqrt(sum(x * x for x in qv)) or 1.0
    qv = [x / qn for x in qv]
    qt = _tokens(query)
    idf = kb["_idf"]
    q_weight = sum(idf.get(t, 1.0) for t in qt) or 1.0

    scored = []
    for c in kb["chunks"]:
        cos = sum(a * b for a, b in zip(qv, c["_vec"]))
        # IDF-weighted keyword overlap: rare words (e.g. "adrenal", "in-patient") count more than "cancer".
        kw = sum(idf.get(t, 1.0) for t in qt & c["_tokens"]) / q_weight
        # Section titles are strong signals ("Included in the Out-Patient Program", "Cost of ...").
        head = sum(idf.get(t, 1.0) for t in qt & c["_head"]) / q_weight
        scored.append(((0.8 * cos + 0.1 * kw + 0.1 * head) * c["_weight"], cos, c))
    scored.sort(key=lambda s: s[0], reverse=True)
    top = [(s, cos, c) for s, cos, c in scored[:top_k] if cos >= config.MIN_SIMILARITY]

    # Neighbour expansion: a section often continues in the next chunk (e.g. Out-Patient then In-Patient
    # inclusions), so the chunk after each of the 3 best hits is added when it is not already present.
    by_id = {c["id"]: c for c in kb["chunks"]}
    present = {c["id"] for _, _, c in top}
    for s, cos, c in list(top[:3]):
        page, num = c["id"].rsplit("-c", 1)
        nxt = by_id.get(f"{page}-c{int(num) + 1}")
        if nxt and nxt["id"] not in present:
            present.add(nxt["id"])
            top.append((s - 0.01, cos, nxt))

    return [
        {"score": round(s, 3), "similarity": round(cos, 3),
         **{k: v for k, v in c.items() if not k.startswith("_") and k != "embedding"}}
        for s, cos, c in top
    ]
