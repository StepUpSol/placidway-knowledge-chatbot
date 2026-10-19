"""Content ingestion: fetch the 7 source pages, clean them, chunk them and embed them.

Run:  python -m app.ingest            (writes data/kb-part*.json.gz)
"""
import glob
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app import config
from app.llm import embed_texts

# Page furniture that adds noise but no facts (booking widgets, buttons, share bars).
NOISE_SELECTORS = [
    "script", "style", "noscript", "svg", "iframe", "select", "option", "form", "button",
    "header", "footer", "nav", "input", "textarea",
    "[class*=modal]", "[id*=modal]", "[class*=breadcrumb]", "[class*=cookie]",
]
NOISE_LINES = {
    "chat with center", "share this listing", "copy link", "read more", "request a video call",
    "select time", "select date", "select time zone", "confirm consultation", "book a virtual consultation",
    "request virtual consultation", "video", "phone", "actions", "search", "ai assistant", "view all",
    "load more", "see more", "show more", "get a free quote", "send", "submit",
}
HEADING_TAGS = ["h1", "h2", "h3", "h4"]
_ROBOTS = {}


def robots_allows(url: str) -> bool:
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    if robots_url not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        # Fetched with our own User-Agent: the stdlib default UA is blocked by some sites,
        # which robotparser would wrongly read as "disallow everything".
        resp = requests.get(robots_url, headers={"User-Agent": config.USER_AGENT}, timeout=20)
        if resp.status_code >= 400:
            return resp.status_code == 404  # no robots.txt means everything is allowed
        rp.parse(resp.text.splitlines())
        _ROBOTS[robots_url] = rp
        time.sleep(config.REQUEST_DELAY_SECONDS)
    return _ROBOTS[robots_url].can_fetch(config.USER_AGENT, url)


def fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def html_to_lines(html: str):
    """Return (title, lines). Headings are prefixed with '## ', table rows joined with ' | '."""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    for sel in NOISE_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()

    # Flatten tables into one readable line per row so prices stay next to their labels.
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with(soup.new_string("\n" + "\n".join(rows) + "\n"))

    # Listing cards ("Price starting from $X | Package title | Clinic | City") put the price BEFORE the title.
    # Flatten every card into one self-contained line so a price can never be attached to the wrong title.
    cards = [el for el in soup.select(".splide__slide, [class*=card]") if "Price starting from" in el.get_text()]
    cards = [el for el in cards if not any(other is not el and other in el.descendants for other in cards)]
    for card in cards:
        parts = [p for p in card.get_text("\n", strip=True).split("\n") if p.strip()]
        price = next((p for p in parts if p.strip().startswith("$")), None)
        rest = [p for p in parts if p != price and p.lower() != "price starting from"
                and not re.fullmatch(r"[\d.,]+[KkMm]?", p.strip())]  # drop view counters like "20.2K"
        line = "Listing: " + " | ".join(rest) + (f" | Price starting from {price}" if price else " | no price shown")
        card.replace_with(soup.new_string("\n" + line + "\n"))

    for h in soup.find_all(HEADING_TAGS):
        text = h.get_text(" ", strip=True)
        h.replace_with(soup.new_string(f"\n## {text}\n" if text else "\n"))

    body = soup.body or soup
    raw_lines = body.get_text("\n").split("\n")
    lines, prev = [], None
    for ln in raw_lines:
        ln = re.sub(r"\s+", " ", ln).strip()
        if len(ln) < 2 or ln.lower() in NOISE_LINES or ln == prev:
            continue
        lines.append(ln)
        prev = ln
    return title, lines


def chunk_lines(lines, page_title):
    """Split on headings, then pack lines into chunks of at most CHUNK_MAX_CHARS."""
    sections, heading, buf = [], page_title, []
    for ln in lines:
        if ln.startswith("## "):
            if buf:
                sections.append((heading, buf))
            heading, buf = ln[3:], []
        else:
            buf.append(ln)
    if buf:
        sections.append((heading, buf))

    chunks = []
    for heading, sec_lines in sections:
        current = []
        for ln in sec_lines:
            if current and sum(len(x) + 1 for x in current) + len(ln) > config.CHUNK_MAX_CHARS:
                chunks.append((heading, " \n".join(current)))
                # Keep a short last line (often a label such as "Price:") as overlap; never repeat long paragraphs.
                current = current[-1:] if len(current[-1]) < 160 else []
            current.append(ln)
        if current:
            text = " \n".join(current)
            if len(text) > 40:  # skip fragments that carry no information
                chunks.append((heading, text))
    return chunks


def build_knowledge_base(verbose=True):
    records, seen = [], set()
    pages = []
    for i, src in enumerate(config.SOURCE_URLS):
        url = src["url"]
        if not robots_allows(url):
            print(f"skip (robots.txt): {url}", file=sys.stderr)
            continue
        if i:
            time.sleep(config.REQUEST_DELAY_SECONDS)
        html = fetch(url)
        title, lines = html_to_lines(html)
        page_chunks = chunk_lines(lines, title)
        kept = 0
        for heading, text in page_chunks:
            digest = hashlib.sha1(text.encode()).hexdigest()
            if digest in seen:  # the same boilerplate appears on several pages
                continue
            seen.add(digest)
            records.append({
                "id": f"p{i + 1}-c{kept + 1}",
                "url": url,
                "page_title": title,
                "page_type": src["type"],
                "heading": heading,
                "text": text,
            })
            kept += 1
        pages.append({"url": url, "title": title, "type": src["type"], "chunks": kept})
        if verbose:
            print(f"{kept:3d} chunks  {title}", file=sys.stderr)

    vectors = embed_texts([f"{r['page_title']} | {r['heading']}\n{r['text']}" for r in records])
    for r, v in zip(records, vectors):
        r["embedding"] = [round(x, 5) for x in v]

    return {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "embed_model": config.EMBED_MODEL,
        "embed_dimensions": config.EMBED_DIMENSIONS,
        "pages": pages,
        "chunks": records,
    }


def save(kb, data_dir):
    """Store chunks without embeddings (small, diff-friendly) in parts; vectors are recomputed on first load."""
    for old in glob.glob(os.path.join(data_dir, "kb-part*.json.gz")):
        os.remove(old)
    chunks = [{k: v for k, v in c.items() if k != "embedding"} for c in kb["chunks"]]
    size = config.KB_PART_SIZE
    for n, start in enumerate(range(0, len(chunks), size), 1):
        part = {**kb, "chunks": chunks[start:start + size]}
        data = json.dumps(part, ensure_ascii=False, indent=1).encode("utf-8")
        path = os.path.join(data_dir, f"kb-part{n}.json.gz")
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
            f.write(data)


if __name__ == "__main__":
    kb = build_knowledge_base()
    save(kb, config.DATA_DIR)
    print(f"wrote {len(kb['chunks'])} chunks to {config.DATA_DIR}", file=sys.stderr)
