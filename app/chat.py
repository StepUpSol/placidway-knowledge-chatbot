"""The answer pipeline: understand -> retrieve -> answer -> verify.

Grounding rules are enforced twice: once in the prompt, and once in code (verify_prices),
so a price that is not literally on a source page can never reach the user.
"""
import json
import re
import sys
from datetime import datetime, timezone

from app import config
from app.llm import chat_json
from app.retriever import search

GAPS = []   # questions the bot could not answer (also printed to the server log)
LEADS = []  # quote requests captured in the chat (also printed to the server log)

ANALYZE_PROMPT = """You route visitor messages for a medical tourism website chatbot.
The bot can only use content from PlacidWay pages about the ITC Immunity Therapy Center
(alternative cancer treatment in Tijuana, Mexico), its treatment packages and price list,
and a PlacidWay page comparing alternative medicine prices abroad.

Return JSON with:
- "language": the language the visitor wrote in, e.g. "English", "Spanish", "Arabic".
- "standalone_question": the visitor's latest message rewritten in English as a complete question,
  resolving references like "it", "there" or "what about recovery" using the conversation history.
- "category": one of
   "question"          a question that the website content might answer (treatments, costs, packages, clinic, location, doctors, what is included, comparisons)
   "medical_advice"    asks whether a treatment is safe/right for their personal condition, asks for a diagnosis, dosage or prognosis
   "booking"           wants to book, schedule an appointment, get a quote or be contacted
   "out_of_scope"      unrelated to medical travel or this clinic (weather, sports, coding, jokes...)
   "greeting"          hello, thanks, bye, small talk
- "claimed_price": if the visitor asserts a price as a fact ("I heard it costs $50"), copy that claim, else null.
Return only JSON."""

ANSWER_PROMPT = """You are the PlacidWay website assistant. You answer ONLY from the SOURCES provided.

Hard rules:
1. Use only facts found in the SOURCES. Never use outside knowledge about clinics, treatments, prices, doctors or medicine.
2. If the SOURCES do not contain the answer, say plainly that these pages don't cover it and suggest requesting
   a free quote from PlacidWay. Do not guess. If the SOURCES contain closely related facts (e.g. program length or
   follow-up timing when asked about recovery), you may share them, clearly labelled as related, with status "partial".
3. Prices: copy every amount exactly as written in the SOURCES, with its currency (USD) and context
   (e.g. "starting from", "3-week Out-Patient program", "approx. per visit"). Never calculate, convert, round or estimate a price.
4. Comparisons: compare only items that both appear in the SOURCES. If one side is missing, say so.
5. If the visitor claims a price or fact that the SOURCES contradict or do not contain, politely say it does not match the
   website and give what the SOURCES actually say. Never confirm an unsupported claim.
6. When the message type is "medical_advice" (their own health, safety, suitability, diagnosis, prognosis):
   start by saying you can't give medical advice or judge whether a treatment is safe for them, and recommend they
   speak with their own doctor. Do not describe the treatment as safe or unsafe. You may then add, from the SOURCES,
   that the clinic reviews medical records and assesses eligibility before treatment. Use status "declined".
7. Booking: you cannot book appointments or see availability. Point them to the quote / consultation form ({quote_url})
   or PlacidWay contact page ({contact_url}).
8. Out of scope topics: politely decline in one sentence and say what you can help with. Use status "declined".
9. Prefer the most specific SOURCES: for "what is included" use the sections listing inclusions (and exclusions/extra costs
   if present); describe each price exactly as the page labels it (e.g. a clinic's listed price), never adding units
   such as "per session" that the page does not state. Lines starting with "Listing:" are self-contained cards for
   OTHER clinics or packages: each price belongs only to the title on the same line.
10. Greetings, thanks and small talk (message type "greeting"): reply in one or two friendly sentences and say what you
   can help with (ITC Immunity Therapy Center's treatment packages, prices, what is included, location). Never say
   information is missing for a greeting. Use status "answered" and offer_quote false.
11. Write in {language}. Keep it short and scannable: 1 short paragraph or a few bullets. Do not paste URLs in the answer text;
   sources are shown separately.

Return JSON:
{{"answer": "<markdown answer>",
  "used_sources": ["S1", ...],      // the SOURCE ids that support the answer; [] if none
  "status": "answered" | "partial" | "not_in_content" | "declined",
  "offer_quote": true|false}}         // true when a personalised quote or human follow-up would help
"""

FALLBACK = {
    "English": "I'm sorry, I don't have reliable information about that on these PlacidWay pages. "
               "You can request a free, personalised quote from PlacidWay and their team will answer you directly.",
}

_MONEY = re.compile(r"(?:US\$|\$|USD\s?)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|US dollars|dollars)", re.I)


def _amounts(text):
    """Normalised numeric values of every money amount in a text, e.g. '$18,995' -> '18995'."""
    vals = set()
    for m in _MONEY.findall(text or ""):
        num = re.sub(r"[^\d.]", "", m).rstrip(".")
        if num:
            vals.add(num.split(".")[0])
    return vals


def verify_prices(answer, sources_text):
    """Return the list of money amounts in the answer that do not appear in the cited sources."""
    allowed = {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d,]*", sources_text)}
    return sorted(a for a in _amounts(answer) if a not in allowed)


def _format_sources(hits):
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(f"[S{i}] Page: {h['page_title']} ({h['page_type']}) | Section: {h['heading']}\n{h['text']}")
    return "\n\n".join(blocks)


def _history_text(history):
    lines = []
    for turn in history[-6:]:
        role = "Visitor" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {str(turn.get('content', ''))[:600]}")
    return "\n".join(lines) or "(none)"


def _log(kind, payload):
    record = {"type": kind, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)
    try:
        with open(f"/tmp/{kind}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return record


def answer(message, history=None):
    history = history or []
    message = (message or "").strip()[:1000]
    if not message:
        return {"answer": "Please type a question.", "sources": [], "status": "declined"}

    route = chat_json(ANALYZE_PROMPT, f"Conversation so far:\n{_history_text(history)}\n\nLatest visitor message:\n{message}",
                      max_tokens=300)
    language = route.get("language") or "English"
    question = route.get("standalone_question") or message
    category = route.get("category") or "question"

    hits = [] if category in ("out_of_scope", "greeting") else search(question)
    sources_block = _format_sources(hits) if hits else "(no relevant sources found)"

    system = ANSWER_PROMPT.format(language=language, quote_url=config.QUOTE_URL, contact_url=config.CONTACT_URL)
    user = (f"Conversation so far:\n{_history_text(history)}\n\n"
            f"Visitor message: {message}\n"
            f"Interpreted question: {question}\n"
            f"Message type: {category}\n"
            f"Visitor's price claim to check: {route.get('claimed_price') or 'none'}\n\n"
            f"SOURCES:\n{sources_block}")
    result = chat_json(system, user)

    # Code-level guard: every price in the answer must appear in the sources the model cited.
    cited = [hits[int(s[1:]) - 1] for s in result.get("used_sources", [])
             if re.fullmatch(r"S\d+", str(s)) and 0 < int(s[1:]) <= len(hits)]
    cited_text = "\n".join(h["text"] for h in cited) + "\n" + "\n".join(h["heading"] for h in cited)
    # Amounts the visitor typed (e.g. a wrong price they heard) may be quoted back when correcting them.
    bad = verify_prices(result.get("answer", ""), cited_text + "\n" + message)
    if bad:
        retry_user = user + (f"\n\nYour previous answer contained amounts not found in the cited sources: {', '.join(bad)}. "
                             "Answer again using only amounts that appear verbatim in the SOURCES, and cite them.")
        result = chat_json(system, retry_user)
        cited = [hits[int(s[1:]) - 1] for s in result.get("used_sources", [])
                 if re.fullmatch(r"S\d+", str(s)) and 0 < int(s[1:]) <= len(hits)]
        cited_text = "\n".join(h["text"] for h in cited)
        if verify_prices(result.get("answer", ""), cited_text + "\n" + message):
            result = {"answer": FALLBACK["English"], "status": "not_in_content", "offer_quote": True}
            cited = []

    status = result.get("status", "answered")
    if category in ("out_of_scope", "greeting"):
        status = "declined" if category == "out_of_scope" else "answered"
    if status in ("answered", "partial") and not cited:
        # An answer with no supporting source is not allowed to claim it answered.
        status = "not_in_content" if category == "question" else status

    sources, seen = [], set()
    for h in cited:
        if h["url"] not in seen:
            seen.add(h["url"])
            sources.append({"title": h["page_title"], "url": h["url"]})

    if category == "question" and (status == "not_in_content" or not hits):
        GAPS.append(_log("unanswered", {"question": message, "interpreted": question, "language": language}))

    return {
        "answer": result.get("answer") or FALLBACK["English"],
        "sources": sources,
        "status": status,
        "category": category,
        "language": language,
        # Offer the quote form only when it helps: booking requests, or questions the pages can't fully answer.
        "offer_lead": category == "booking" or (category == "question" and status in ("partial", "not_in_content")),
        "show_disclaimer": category in ("question", "medical_advice") and status != "declined",
        "quote_url": config.QUOTE_URL,
    }


def save_lead(name, email, note=""):
    name, email = (name or "").strip()[:100], (email or "").strip()[:200]
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return {"ok": False, "error": "Please enter your name and a valid email."}
    LEADS.append(_log("lead", {"name": name, "email": email, "note": (note or "")[:500]}))
    return {"ok": True}
