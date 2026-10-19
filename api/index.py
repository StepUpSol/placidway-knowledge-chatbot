"""HTTP layer (Flask). Vercel runs this file as a Python serverless function; locally: python api/index.py"""
import hmac
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from app import chat, config, retriever  # noqa: E402
from app.ingest import build_knowledge_base  # noqa: E402

PUBLIC_DIR = os.path.join(config.ROOT, "public")
app = Flask(__name__)


class _RestorePath:
    """vercel.json rewrites /api/<x> to /api/index?__path=/api/<x>; put the original path back for Flask routing."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        from urllib.parse import parse_qs, urlencode
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        original = query.pop("__path", [None])[0]
        if original:
            environ["PATH_INFO"] = original
            environ["QUERY_STRING"] = urlencode(query, doseq=True)
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _RestorePath(app.wsgi_app)


def _authorized():
    token = request.headers.get("X-Admin-Token") or request.args.get("token", "")
    return bool(config.ADMIN_TOKEN) and hmac.compare_digest(token, config.ADMIN_TOKEN)


@app.get("/")
def home():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.get("/api/health")
def health():
    kb = retriever.load_kb()
    return jsonify(ok=True, built_at=kb["built_at"], chunks=len(kb["chunks"]), pages=kb["pages"])


@app.post("/api/chat")
def chat_endpoint():
    data = request.get_json(silent=True) or {}
    history = data.get("history") if isinstance(data.get("history"), list) else []
    try:
        return jsonify(chat.answer(data.get("message", ""), history))
    except Exception:
        traceback.print_exc()
        return jsonify(answer="Sorry, something went wrong on my side. Please try again in a moment.",
                       sources=[], status="error"), 500


@app.post("/api/lead")
def lead_endpoint():
    data = request.get_json(silent=True) or {}
    result = chat.save_lead(data.get("name"), data.get("email"), data.get("note", ""))
    return jsonify(result), (200 if result["ok"] else 400)


@app.post("/api/refresh")
def refresh_endpoint():
    """Re-crawl the 7 pages and swap the knowledge base in place (admin only)."""
    if not _authorized():
        return jsonify(error="unauthorized"), 401
    kb = build_knowledge_base(verbose=False)
    retriever.set_kb(kb)
    return jsonify(ok=True, built_at=kb["built_at"], chunks=len(kb["chunks"]))


@app.get("/api/gaps")
def gaps_endpoint():
    """Questions the bot could not answer on this server instance (admin only). Full history is in the server logs."""
    if not _authorized():
        return jsonify(error="unauthorized"), 401
    return jsonify(unanswered=chat.GAPS[-200:], leads=chat.LEADS[-200:])


if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", "8000")), debug=False)
