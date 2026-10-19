# PlacidWay Website Knowledge Chatbot

A small, reliable chatbot that answers visitor questions using **only** the 7 PlacidWay pages provided in the
assessment, cites the page every answer comes from, and says "I don't know" instead of guessing.

**Live demo:** https://placidway-knowledge-chatbot-chi.vercel.app

## What it does

- Answers questions about ITC Immunity Therapy Center (Tijuana), its treatment packages, price list and the
  alternative medicine price comparison page.
- Every answer shows clickable source links to the PlacidWay page(s) it used.
- Prices are always quoted exactly as written on the page, with currency and context ("3-week Out-Patient program",
  "starting from", "approx. per visit"). A code-level check blocks any amount that is not on a cited page.
- Honest "I don't know" when the pages do not cover something, with a pointer to PlacidWay's free quote form.
- Refuses to give medical advice and recommends speaking with a doctor.
- Declines out-of-scope questions politely and does not accept wrong premises ("I heard it costs $50").

**Nice-to-haves included:** answers in the user's language, follow-up questions using chat history, a one-time medical
disclaimer, in-chat lead capture (name and email) when someone wants a quote, and logging of unanswered questions.

## Architecture

```
            ingest (python -m app.ingest)                         every request (/api/chat)
 7 URLs ──► robots.txt check, 1 req/s ──► clean HTML ──►   question + history
            (drop menus, booking widget, forms;             │
             tables -> "a | b | c" rows;                    ▼
             listing cards -> one line each)          1. Route: language, standalone English question,
                       │                                 category (question / medical / booking / out of scope),
                       ▼                                 any price claim to check            [gpt-4.1-mini]
            chunk by section heading (≤1,100 chars)         │
                       │                                    ▼
                       ▼                              2. Retrieve: embeddings (cosine) + IDF keyword and
            data/kb-part*.json.gz (189 chunks)         section-title boost, top 10 + next-chunk expansion
                       │                                    │
                       ▼                                    ▼
            embeddings built once per server ──►      3. Answer from numbered SOURCES only, JSON output
            (text-embedding-3-small, 512 dims,           with cited source ids and a status       [gpt-4.1-mini]
             cached in /tmp)                                │
                                                            ▼
                                                      4. Verify: every $ amount must appear in a cited source
                                                         (retry once, else safe "I don't know"); answers with
                                                         no cited source cannot claim "answered"
                                                            │
                                                            ▼
                                                      answer + source links + status, gaps logged
```

| File | Role |
|---|---|
| `app/config.py` | Source URLs, models, chunk size, thresholds. One place to change things. |
| `app/ingest.py` | Polite crawler, HTML cleaning, chunking, embedding. |
| `app/retriever.py` | Hybrid search over chunks. |
| `app/chat.py` | Prompts, answer pipeline, price verification, gap and lead logging. |
| `app/llm.py` | OpenAI wrapper (swap provider here). |
| `api/index.py` | Flask HTTP API, also the Vercel serverless entry point. |
| `public/index.html` | Chat UI (plain HTML/JS, no build step). |
| `scripts/run_tests.py` | Runs the 8 assessment questions plus extras, writes `TEST_RESULTS.md`. |

## Tools and why

- **Python + Flask:** small, readable, easy to change live in a review call. No framework magic.
- **OpenAI `gpt-4.1-mini`, temperature 0, JSON mode:** strong instruction following for grounding rules, fast and cheap.
  Model name is an env var (`CHAT_MODEL`).
- **`text-embedding-3-small` at 512 dimensions:** plenty for ~190 chunks; vectors are computed once per server start
  (one batched call, about 2 seconds) so the repo only stores readable text.
- **No vector database:** the corpus is tiny, so in-memory cosine search is simpler, faster and has fewer moving parts.
- **Hybrid retrieval:** pure embeddings missed exact section matches ("Included in the Out-Patient Program"), so an
  IDF-weighted keyword score and a section-title boost were added.
- **Vercel:** free hosting, HTTPS, deploys from the repo.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
python -m app.ingest          # optional: re-crawl the 7 pages (data/ is already included)
python api/index.py           # http://localhost:8000
python scripts/run_tests.py   # writes TEST_RESULTS.md
```

## Deploy (Vercel)

1. Import the repo in Vercel (no build settings needed; `vercel.json` routes everything to `api/index.py`).
2. Add environment variables: `OPENAI_API_KEY` (required), `ADMIN_TOKEN` (any long random string, protects admin endpoints).
3. Deploy.

## Keeping content fresh

- `python -m app.ingest` re-crawls the 7 pages and rewrites `data/kb-part*.json.gz`; commit and the site redeploys.
- `.github/workflows/refresh-content.yml` does the same automatically every Monday (add `OPENAI_API_KEY` as a repo secret).
- `POST /api/refresh` with header `X-Admin-Token` re-crawls on the live server without a redeploy.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` `{message, history}` | Returns `answer`, `sources[]`, `status` (`answered`, `partial`, `not_in_content`, `declined`), `offer_lead`, `show_disclaimer`. |
| `POST /api/lead` `{name, email, note}` | Records a quote request. |
| `GET /api/health` | Knowledge base build time and chunk counts per page. |
| `POST /api/refresh` (admin) | Re-crawl the sources. |
| `GET /api/gaps` (admin) | Unanswered questions and leads on this server instance. |

Unanswered questions and leads are also written as JSON lines to the server log (`{"type": "unanswered", ...}`), visible
in Vercel's runtime logs.

## Crawling etiquette

Only the 7 listed URLs are fetched, never links found on them. `robots.txt` is checked first, requests identify the bot
with a clear User-Agent, and there is a 1.2 second pause between requests.

## Known limitations

See `WRITEUP.md`.
