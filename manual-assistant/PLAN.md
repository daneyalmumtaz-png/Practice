# Manual Assistant: build plan

## What the Companion prototype contains

`main` carries `companion.html`, `Dockerfile`, `docker-compose.yml`,
`Create Desktop Shortcut.bat`, `companion.ico` and `chunks.json`. All four text files have
been read. `server.py`, `README.txt` and `Start Companion.bat` are not in the upload, so the
backend was not available to read; the new backend is written from the task description
instead, against the front-end contract visible in `companion.html`.

`chunks.json` and `manual.pdf` are not read, copied or referenced. `chunks.json` is present
in the repository because it was uploaded there, and no code path in the new app looks outside
`data/manuals/`. `manual.pdf` is not in the repository at all.

### Reused from Companion

- Streamed answers, so the first words appear before the model has finished.
- `[n]` citations rendered as links to `<pdf>#page=<n>`, opening the PDF at the cited page.
  Kept, with the citation also scrolling to and highlighting the matching section card.
- The reference list under the answer, each entry numbered and linked. Extended into the
  section cards, which now also carry section path, printed page label, snippet and preview.
- The small Markdown renderer limited to ordered lists, unordered lists, paragraphs and bold.
  Kept as the only formatting the model output is allowed to produce, but it builds DOM nodes
  with `textContent` rather than assembling an HTML string.
- From `docker-compose.yml`: the `ollama` service with no published port, the
  `ollama serve` then `ollama pull` entrypoint, the `ollama list` healthcheck, the
  `depends_on: service_healthy` gate, the named model volume, and the commented NVIDIA GPU
  block. The pull step is extended to fetch `bge-m3` as well as `LLM_MODEL`.

### Confirmed against the code

- `Dockerfile` does `COPY server.py companion.html manual.pdf ./`, so exactly one manual is
  baked into the image. The new app keeps manuals in a volume at `data/manuals/` and indexes
  many of them.
- `Dockerfile` sets `ENV HOST=0.0.0.0`, and `docker-compose.yml` publishes `8000:8000`, so the
  app is reachable on every interface with no proxy and no login. The new app defaults to
  `127.0.0.1`, listens on `0.0.0.0` only inside the container, and publishes 80 and 443 only,
  through Caddy.
- `companion.html` hard-codes the title, the titlebar, `header h1`, the greeting
  "Hello, I'm Companion, a virtual assistant", and the single example question, which names
  another product's report. All of this moves to `config.yaml`.
- `refsHtml()` prints `page ' + esc(r.page)`, a single page value with no section path and no
  distinction between the printed label and the PDF page number. The new cards show manual
  title, section path, printed label and PDF page as separate fields.
- The wire format is one line of JSON metadata followed by raw answer text. It is replaced by
  Server-Sent Events with named `sources`, `token`, `done` and `error` events, which gives the
  front end a defined failure path instead of `catch` printing one generic sentence.
- `esc()` escapes only `&`, `<` and `>`, and the result is then written through `innerHTML`
  together with an `href` built from server data. The new front end inserts all server and
  model text as text nodes and builds links with `setAttribute`.
- Reading the remaining findings (500 character cut, split tables, keyword-only English
  search, no follow-ups, no login, no logging, no feedback, hang on a bad PDF) requires
  `server.py`, which is missing. They are taken as stated in the task description and each is
  fixed below.

## Review findings mapped to fixes

| Finding in Companion | Fix in Manual Assistant |
| --- | --- |
| One PDF at a time, newest replaces the rest | `documents` table, many manuals, per-document ingest, `current` / `superseded` status |
| Product name, greeting, system prompt hard-coded | everything in `config.yaml`, read at startup, nothing product-specific in Python or HTML |
| Text cut to 500 characters | whole chunks only, selection by a ~3,000 token budget, no truncation anywhere |
| Tables split from headers | `page.find_tables()`, table to Markdown with header row and caption, stored as one unsplittable chunk |
| Wrong section titles and page numbers | section path from `doc.get_toc()` (font-size and numbered-heading fallback), printed label from `page.get_label()` plus 1-based PDF page, both shown |
| Keyword only, English only | hybrid BM25 (English stemming plus Finnish lemmatisation) and `bge-m3` embeddings, fused with RRF k=60, plus model-generated English query for non-English questions |
| No follow-ups, login, logging, feedback | last 3 turns rewritten to a standalone question, bcrypt users with signed session cookies and roles, SQLite question log, thumbs up/down |
| Binds all interfaces, hangs on a bad PDF | default bind `127.0.0.1`, Docker publishes only 80/443 through Caddy, ingest failures recorded as `failed` with the error and the app keeps serving other documents |

## File tree

```
manual-assistant/
  PLAN.md
  README.md                  run locally, Docker, cloud VM, first admin, uploads,
                             switch to a cloud model, eval, tests, back up data volume
  requirements.txt           exact pins
  config.yaml                app name, products, greeting, 3 examples, system prompt,
                             retrieval settings
  users.yaml.example         bcrypt hashes, roles
  .env.example               every setting documented
  Dockerfile                 python:3.12-slim, non-root, healthcheck
  docker-compose.yml         app + ollama (no published port) + caddy (80/443 only)
  Caddyfile                  automatic HTTPS for APP_DOMAIN
  pyproject.toml             pytest config only
  app/
    __init__.py
    config.py                config.yaml + environment, one Settings object
    logsetup.py              structured JSON logs to stdout
    db.py                    sqlite schema, migrations, retention sweep
    auth.py                  AuthBackend protocol, LocalUsersBackend (bcrypt +
                             itsdangerous cookies), require_user / require_admin.
                             Written so an OIDC backend (Entra ID) can replace it.
    pdfutil.py               page text, page.get_label(), doc.get_toc(), heading
                             fallback, boilerplate detection, TOC/index page skip,
                             page.find_tables() to Markdown
    chunker.py               section-aware ~1200 char chunks, 150 char overlap,
                             table chunks never split
    ingest.py                document ingest pipeline + `python -m app.ingest` CLI
    lang.py                  language detection, English Snowball stemming,
                             Finnish simplemma lemmatisation, shared tokeniser
    embed.py                 Ollama /api/embed client, numpy .npy store on disk,
                             graceful degradation when unavailable
    search.py                BM25 (section path weighted x2) + cosine + RRF k=60,
                             dedupe by (doc, page), top 8 to UI, token budget to model
    llm.py                   OpenAI-compatible chat completions, streaming, timeouts
    answer.py                standalone-question rewrite, English query for FI
                             questions, prompt assembly, source numbering
    pages.py                 110 dpi page PNG with on-disk cache
    ratelimit.py             20 questions/minute/user
    main.py                  FastAPI app and all routes
    eval.py                  `python -m app.eval`
  static/
    login.html  index.html  admin.html
    style.css   chat.js      admin.js
  eval/
    questions.csv            header + 5 rows, expected_pages EMPTY
  tests/
    conftest.py              synthetic PDF built with PyMuPDF: bookmarks, repeated
                             footer, roman then arabic page labels, one table,
                             one TOC page with dot leaders, one index page
    test_ingest.py  test_search.py  test_api.py  test_security.py
  data/                      created at runtime, gitignored
    manuals/  vectors/  pagecache/  app.db
```

## Data model (SQLite)

- `documents` id, title, product, version, status, sha256, page_count, ingest_status,
  ingest_error, uploaded_at, uploaded_by, licence_confirmed_by, licence_confirmed_at,
  filename
- `chunks` id, doc_id, section_path, page_label, pdf_page, chunk_type, text, char_len
- `questions` id, user, asked_at, question, language, source_ids, answer_chars, latency_ms
- `feedback` id, question_id, rating, comment, created_at
- embeddings: one `data/vectors/<doc_id>.npy` float32 matrix plus a `chunk_id` order column,
  not in SQLite

## API

`GET /` chat, `GET /admin`, `GET /login` + `POST /login`, `POST /logout`,
`POST /api/search`, `POST /api/ask` (SSE: `sources` then `token` then `done`, `error` on
failure), `GET /files/{doc_id}` (FileResponse, Range), `GET /api/page/{doc_id}/{pdf_page}.png`,
`GET /api/docs`, `POST /api/feedback`, `GET /healthz`,
`/api/admin/upload|reindex|delete|documents` behind the admin role.

## Dependency pins

python 3.12. fastapi 0.143.0, uvicorn 0.54.0, pymupdf 1.28.2, rank-bm25 0.2.2,
snowballstemmer 3.1.1, simplemma 2.0.0, numpy 2.5.4, httpx 0.28.1, pyyaml 6.0.3,
bcrypt 5.0.0, itsdangerous 2.2.0, python-multipart 0.0.32, pytest 9.1.1,
pytest-asyncio 1.4.0.

## Stages

Tests run at the end of every stage; failures are fixed before the next one starts.

1. **Skeleton, config, database, ingestion.** `config.py`, `logsetup.py`, `db.py`,
   `pdfutil.py`, `chunker.py`, `ingest.py`, CLI, `tests/conftest.py`, `test_ingest.py`.
   Covers page labels, boilerplate removal, TOC and index skip, whole tables with headers,
   section paths, SHA-256 caching, `failed` status.
2. **Search and evaluation.** `lang.py`, `embed.py`, `search.py`, `eval/questions.csv`,
   `app/eval.py`, `test_search.py`. Covers BM25 plus RRF ranking, Finnish lemmatisation,
   section-path weighting, superseded documents excluded, BM25-only fallback when the
   embedding model is down.
3. **Model calls and streaming API.** `llm.py`, `answer.py`, `pages.py`, `main.py` routes,
   `test_api.py` against a stub chat server. Covers the SSE event order, whole chunks in the
   prompt, "Not found in the uploaded manuals", Range requests on `/files`.
4. **Chat and admin front end.** `static/*`. Section cards first, streamed answer below,
   `[n]` scroll-and-highlight, "Answer based on N sources" versus the warning style, page
   preview, thumbs up/down, phone layout, everything escaped with `textContent`.
5. **Login, roles, limits, logging, retention.** `auth.py`, `ratelimit.py`, 16 kB body cap,
   model-call timeouts, question log and the daily retention sweep, `test_security.py`.
6. **Deployment.** `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.env.example`,
   `README.md`.

Then: start the app on `127.0.0.1`, upload the synthetic test PDF, ask 3 questions through the
API (English, Finnish, follow-up) and show the output.

## What I expect not to be able to verify here

This container has no Ollama and no GPU, and pulling `bge-m3` (around 1.2 GB) and
`llama3.1:8b` (around 4.7 GB) is not realistic inside the session. So:

- Embedding search and real model answers will be exercised against a local stub that speaks
  the Ollama embeddings API and the OpenAI-compatible chat completions API. The code paths,
  request shapes and streaming are tested; real `bge-m3` vector quality and real
  `llama3.1:8b` output are not.
- The BM25-only fallback is tested with the embedding service genuinely absent, which is the
  state of this container.
- Docker build and compose up are not run here; the Dockerfile and compose file are written
  to be reviewed and run on your side.

I will repeat this list in the final report.

## Open question

`config.yaml` ships a greeting and 3 example questions. Bentley command names, ribbon tabs,
menu paths and page numbers are not invented anywhere, so the example questions are phrased
in plain language ("How do I import borehole data?") rather than naming a specific command. If
you want them to name real commands from your manuals, send me the exact wording and I will
put it in `config.yaml`.
