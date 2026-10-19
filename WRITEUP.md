# Short write-up

## Approach

I treated this as an accuracy problem first and a chatbot second. The bot only ever sees numbered excerpts from the 7
pages, must cite the excerpt ids it used, and returns a status (`answered`, `partial`, `not_in_content`, `declined`).
On top of the prompt, a code check extracts every money amount from the answer and rejects it if that amount is not
written in a cited excerpt. The model gets one retry; if it still fails, the visitor gets an honest "I don't have that"
with the quote form. A price the model invented cannot reach the user.

The biggest accuracy issue was in the HTML, not the model. PlacidWay's listing cards put "Price starting from $X" before
the package title, so a naive text split attached prices to the wrong package. Cards are now flattened into one
self-contained line each ("Listing: title | clinic | city | Price starting from $X"), and tables become row lines, so a
price always stays next to its own label.

## What I would improve with another week

1. **Evaluation set:** 50 to 100 labelled questions with expected answers and source pages, run in CI, scoring
   correctness, citation accuracy and refusal behaviour, so every prompt or retrieval change is measured.
2. **Reranking:** a cross-encoder or LLM rerank step on the top 20 chunks. Retrieval is good but occasionally ranks a
   general section above the most specific one.
3. **Persistent logs:** unanswered questions and leads go to the server log today; I would write them to a database or
   Google Sheet and send leads straight to PlacidWay's CRM.
4. **Streaming responses** for a faster first token, and caching of embeddings for repeated questions.
5. **Structured price extraction:** parse each price table into records (procedure, program, duration, price) so cost
   and comparison questions are answered from structured data rather than text.

## Known limitations

- Answers are only as current as the last crawl (weekly via GitHub Actions, or on demand via `/api/refresh`).
- The comparison page lists other clinics' starting prices; the bot reports them as listed but cannot verify them.
- Logs on Vercel are per instance; `/api/gaps` shows only the current instance, the full history is in runtime logs.
- The first request after a cold start takes about 2 seconds longer while embeddings are built.
- Language detection and translation rely on the model; prices and page names are kept in their original form.
