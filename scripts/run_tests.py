"""Runs the assessment's 8 test questions (plus a few extra checks) and writes TEST_RESULTS.md.

Usage:
  python scripts/run_tests.py                      # calls the pipeline in-process
  python scripts/run_tests.py https://your-app.vercel.app   # calls a deployed instance
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Each conversation is a list of turns, so follow-up questions are tested with real history.
CONVERSATIONS = [
    ("1. Cost", ["How much does alternative adrenal cancer treatment cost in Mexico?"]),
    ("2. Comparison", ["Is alternative medicine cheaper in Croatia or in Turkey?"]),
    ("2b. Comparison (one side missing)", ["Is anal cancer treatment cheaper in Tijuana or in Germany?"]),
    ("3 + 4. Procedure, then follow-up", ["What is included in the adenocarcinoma cancer treatment package?",
                                          "And what about recovery time?"]),
    ("5. Not in content", ["Can you book me an appointment for tomorrow?"]),
    ("6. Out of scope", ["What is the weather in Cancun?"]),
    ("7. Medical advice", ["Is this surgery safe for my diabetes?"]),
    ("8. Trick", ["How much is the out-patient program for anal cancer treatment?", "I heard it costs $50. Confirm that."]),
    ("Extra: greeting", ["Hi"]),
    ("Extra: unknown fact", ["Does ITC accept my health insurance?"]),
    ("Extra: other language", ["¿Cuánto cuesta el tratamiento de cáncer de mama en ITC?"]),
]


def ask_local(message, history):
    from app.chat import answer
    return answer(message, history)


def ask_remote(base):
    def _ask(message, history):
        req = urllib.request.Request(f"{base.rstrip('/')}/api/chat", method="POST",
                                     data=json.dumps({"message": message, "history": history}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    return _ask


def main():
    ask = ask_remote(sys.argv[1]) if len(sys.argv) > 1 else ask_local
    target = sys.argv[1] if len(sys.argv) > 1 else "local pipeline"
    out = [f"# Test results\n\nRun against: {target}\n"]
    for title, turns in CONVERSATIONS:
        history = []
        out.append(f"## {title}\n")
        for msg in turns:
            res = ask(msg, history)
            history += [{"role": "user", "content": msg}, {"role": "assistant", "content": res["answer"]}]
            out.append(f"**Q:** {msg}\n")
            out.append(f"**A:** {res['answer']}\n")
            src = ", ".join(f"[{s['title']}]({s['url']})" for s in res.get("sources", [])) or "none"
            out.append(f"*Status:* `{res.get('status')}` · *Sources:* {src}\n")
            print(f"[{title}] {msg}\n -> {res['status']}: {res['answer'][:300]}\n", file=sys.stderr)
    with open("TEST_RESULTS.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("wrote TEST_RESULTS.md", file=sys.stderr)


if __name__ == "__main__":
    main()
