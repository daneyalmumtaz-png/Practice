# Manual Assistant

An internal web app for asking questions about Bentley manuals in English or
Finnish. Upload the PDFs, ask a question, and get the governing sections first,
then an answer built only from those sections, with a citation after every step
that opens the PDF at the cited page.

Nothing is answered from the model's general knowledge. If the uploaded manuals
do not contain the answer, the app says so and still shows the closest sections.

## Contents

- [What it does](#what-it-does)
- [Try it on a Windows computer](#try-it-on-a-windows-computer)
- [Run locally without Docker](#run-locally-without-docker)
- [Run with Docker](#run-with-docker)
- [Deploy on a cloud virtual machine with a domain name](#deploy-on-a-cloud-virtual-machine-with-a-domain-name)
- [Add the first admin user](#add-the-first-admin-user)
- [Upload manuals](#upload-manuals)
- [Switch to a cloud model](#switch-to-a-cloud-model)
- [Run the evaluation](#run-the-evaluation)
- [Run the tests](#run-the-tests)
- [Back up the data volume](#back-up-the-data-volume)
- [Configuration reference](#configuration-reference)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)

## What it does

For every question, in this order:

1. **Governing sections.** The best matching sections, each with the manual
   title, the section path (for example `Geotechnical > Importing data >
   Borehole import`), the printed page label and the PDF page number, a snippet
   with the matched words highlighted, an **Open page** link and a **Preview**
   button showing an image of the page. This comes from `/api/search` with no
   model call, so it appears at once.
2. **The answer**, streamed below the sections: a one-line summary, then
   numbered steps for a procedure, each with a `[n]` citation pointing at one of
   the sections above. Clicking a citation scrolls to that card and highlights
   it.
3. **"Not found in the uploaded manuals"** when the manuals do not answer the
   question, with the closest section named and still listed.

Search is hybrid: BM25 over text that has been English-stemmed and
Finnish-lemmatised, plus cosine similarity over `bge-m3` embeddings, fused with
Reciprocal Rank Fusion. A Finnish question is also searched with a short English
query, because the manuals are in English. Follow-up questions are rewritten
into standalone questions from the last three turns before searching.

Superseded manuals stay searchable for admins and are never cited in an answer.

## Try it on a Windows computer

The shortest way to see the app working, for one person on their own machine.
Two things to install, then two files to double-click.

**1. Install Python.** Go to <https://www.python.org/downloads/>, download 3.12
or newer, and run the installer. On the very first screen, tick **"Add
python.exe to PATH"** before pressing Install. Missing that tick is the one
thing that goes wrong here.

**2. Install Ollama**, which runs the models on your own machine so no manual
text leaves it. Get it from <https://ollama.com/download>, run the installer,
then open **Command Prompt** and run these two lines. Together they download
about 6 GB, so do it on a decent connection and expect to wait.

```
ollama pull llama3.1:8b
ollama pull bge-m3
```

**3. Set up the app.** Open the `manual-assistant\windows` folder and
double-click **Setup Manual Assistant.bat**. It installs the app's components,
then asks you to choose a username and password. That account is an
administrator, so it can upload manuals. The window tells you when it is
finished. Run this once.

**4. Start the app.** Double-click **Start Manual Assistant.bat**. A black
window opens and your browser follows a few seconds later at
<http://127.0.0.1:8000>. Sign in with the username and password from step 3.

Leave the black window open while you use the app. Closing it stops the app.
Your manuals and index are kept, so starting it again picks up where you left
off. **Create Desktop Shortcut.bat** puts an icon on your Desktop if you would
rather start it from there.

**5. Upload a manual.** Click **Admin** in the top right, choose a PDF, give it
a title, tick the licence confirmation and press **Upload and index**. A long
manual takes a few minutes. Then go back and ask a question.

### What to expect on a laptop

The matching sections appear in about a second. The written answer is the slow
part: `llama3.1:8b` on a laptop with no graphics card takes roughly 20 to 60
seconds for a full set of steps. That is the model thinking, not the app
waiting. The sections are usable immediately and are the part you should trust,
because each one links to the page in the PDF.

The app binds to `127.0.0.1`, which means this computer only. Nobody else on
the office network can reach it. To let colleagues use it, put it on a server
instead: see [Run with Docker](#run-with-docker).

### If something goes wrong

**"Python is not installed, or not on the PATH"** when you double-click Setup.
The tick in step 1 was missed. Re-run the Python installer, choose **Modify**,
and make sure the PATH option is on.

**Your IT policy blocks the installers.** Both Python and Ollama install for a
single user and normally need no administrator rights, but a managed Destia
laptop may still refuse. If so, ask for the two installers through the normal
software request route, or run the app on a server and use it in the browser,
which needs nothing installed on your own machine.

**The app says the model could not be reached.** Ollama is not running. Start
it from the Start menu, wait a few seconds, and ask again. The sections are
still correct meanwhile.

**The app says keyword search only was used.** `bge-m3` is missing. Run
`ollama pull bge-m3`, then press **Re-index** against each manual on the Admin
page so its index is rebuilt with the new model.

**Port 8000 is already in use.** The setup script picks 8099 instead and says
so. If you hit this later, open `local.env` in Notepad and change the `PORT`
line.

**You forgot the password.** Delete `users.yaml` from the `manual-assistant`
folder and run Setup again. Manuals and the index are untouched.

## Run locally without Docker

Needs Python 3.12 and, for real answers, [Ollama](https://ollama.com).

```bash
cd manual-assistant

python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Pull the two models. This downloads several gigabytes the first time.

```bash
ollama pull llama3.1:8b
ollama pull bge-m3
```

Set the two things that have no sensible default, and point the app at Ollama:

```bash
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export LLM_BASE_URL=http://127.0.0.1:11434/v1
export EMBED_BASE_URL=http://127.0.0.1:11434
export COOKIE_SECURE=0               # only because this is plain http on localhost
```

Create a user, then start the app. The two scripts below do the same work as
the Windows batch files, and ask the same questions:

```bash
python scripts/first_run.py          # asks for a username and password
python scripts/start.py              # starts the app and opens your browser
```

Or do it by hand, if you would rather see every step:

```bash
python -m app.auth hash              # type a password, copy the printed hash
cp users.yaml.example users.yaml     # paste the hash into it
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The app binds to `127.0.0.1` by default, so it is
not reachable from the network until you say otherwise.

The app runs without Ollama. Search works, and the page says that keyword search
alone was used. The answer step reports that the model could not be reached, and
the sections are still shown.

## Run with Docker

Needs Docker with the Compose plugin. For the language model to be usable rather
than merely working, give the machine at least 8 GB of RAM and 40 GB of free
disk, or a GPU.

```bash
cd manual-assistant
cp .env.example .env
```

Edit `.env` and set two values:

- `APP_DOMAIN` to the domain this server answers on. Caddy requests a
  certificate for exactly that name, so it must already point here. To try it on
  your own machine without a domain, set `APP_DOMAIN=localhost`; Caddy then
  issues its own certificate and the browser warns once.
- `SESSION_SECRET` to a fresh random string:
  `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

Then:

```bash
docker compose up -d --build
docker compose logs -f ollama        # watch the first model download
```

The first start pulls `llama3.1:8b` and `bge-m3`, which takes a while. The `app`
service waits until both are present. When `docker compose ps` shows `app` as
healthy, open `https://<APP_DOMAIN>/`.

Three services:

| Service | Published ports | Purpose |
| --- | --- | --- |
| `caddy` | **80 and 443, the only published ports** | HTTPS with automatic certificates, reverse proxy |
| `app` | none | this application, data on the `app_data` volume |
| `ollama` | none | the models, on the `ollama_models` volume |

The app and the model are reachable only inside the compose network. Nothing but
Caddy listens on a public port.

## Deploy on a cloud virtual machine with a domain name

Worked through on a fresh Ubuntu 24.04 machine. Any provider works; the only
requirements are a public IP and inbound TCP 80 and 443.

**1. Create the machine.** 4 vCPU, 16 GB RAM, 80 GB disk is comfortable for
`llama3.1:8b` on the processor alone. Smaller works but answers take longer.

**2. Point the domain at it.** Add an `A` record for the name you want, for
example `manuals.destia.example`, to the machine's public IP. Check it before
going further, because Caddy cannot get a certificate until the name resolves:

```bash
dig +short manuals.destia.example
```

**3. Open the firewall to 80 and 443 only.** In the provider's security group or
cloud firewall, allow inbound TCP 22 (while you need SSH), 80 and 443. Nothing
else. Then on the machine:

```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

**4. Install Docker.**

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
```

**5. Copy the app across and configure it.**

```bash
git clone <your repository> && cd <repository>/manual-assistant
cp .env.example .env
nano .env        # set APP_DOMAIN to the name from step 2 and SESSION_SECRET
```

**6. Start it.**

```bash
docker compose up -d --build
docker compose logs -f              # Ctrl-C to stop watching
```

Caddy requests the certificate on first start. Give it a minute, then open
`https://manuals.destia.example/`.

**7. Add yourself as admin.** See the next section.

**8. Check it is healthy.**

```bash
curl -s https://manuals.destia.example/healthz | python3 -m json.tool
docker compose ps
```

To update later:

```bash
git pull && docker compose up -d --build
```

The data volume is untouched by a rebuild, so manuals and the index survive.

## Add the first admin user

Users are a username, a bcrypt hash and a role, either `user` or `admin`. A
`user` can ask questions; an `admin` can also upload, re-index and delete
manuals.

Generate a hash:

```bash
# with Docker
docker compose exec app python -m app.auth hash

# without Docker
python -m app.auth hash
```

Type the password twice. Copy the printed hash, which starts `$2b$12$`.

Then put the user somewhere the app reads. Two options.

**A file on the data volume**, which survives rebuilds:

```bash
docker compose exec app sh -c 'cat > /data/users.yaml' <<'YAML'
users:
  - username: dmumtaz
    role: admin
    password_hash: "$2b$12$replace-with-your-hash"
YAML
docker compose restart app
```

**Or inline in `.env`**, which takes precedence if set. Mind the quoting: the
hash contains `$`, so single-quote it and escape nothing else.

```bash
USERS_YAML='users:
  - username: dmumtaz
    role: admin
    password_hash: "$2b$12$replace-with-your-hash"
'
```

Then `docker compose up -d app`.

Add the rest of the team the same way, with `role: user`. The user list is read
at start-up, so restart `app` after changing it. Removing a user takes effect on
their next request, not when their cookie expires.

Without Docker, the same file is `./users.yaml` next to `config.yaml`, or set
`USERS_FILE` to another path.

## Upload manuals

Through the admin page, which is the normal way:

1. Sign in and open **Admin** in the header.
2. Choose the PDF, give it a title, product and version.
3. Set the status: `current` to be cited in answers, `superseded` to stay
   searchable for admins only.
4. Tick **"Destia is licensed to use this document in this internal tool."**
   This is required. Who ticked it and when is stored against the document.
5. **Upload and index.** A long manual takes a few minutes, most of it
   embedding.

The table then shows pages, chunks and ingest status per document. A PDF that
cannot be read shows `failed` with the error message against it, and every other
manual keeps working. Use **Re-index** after replacing a file, and **Delete** to
remove a manual with its index, its vectors and its page cache.

From the command line, which is easier for a batch:

```bash
# without Docker
python -m app.ingest data/manuals/handbook.pdf \
  --title "OpenRoads Designer Help" \
  --product "OpenRoads Designer" \
  --version "2024.1"

# with Docker: copy the file onto the volume first
docker compose cp handbook.pdf app:/data/manuals/handbook.pdf
docker compose exec app python -m app.ingest /data/manuals/handbook.pdf \
  --title "OpenRoads Designer Help" --product "OpenRoads Designer" --version "2024.1"
```

Add `--status superseded` for an old edition and `--force` to re-parse a file
whose content has not changed. Indexing is cached on the SHA-256 of the file
content, so re-running the command on an unchanged file does nothing.

## Switch to a cloud model

Three environment variables, no code change. The app speaks the OpenAI-compatible
chat completions API.

In `.env`:

```bash
LLM_BASE_URL=https://api.your-provider.example/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=the-model-name
```

Then `docker compose up -d app`. Left blank, these three fall back to the
`ollama` service, so the file works either way.

Embeddings are separate. `EMBED_BASE_URL` points at Ollama's own embeddings API,
which is not the OpenAI one, so leave it on Ollama unless your provider also
serves `bge-m3`. Keeping embeddings local is also the cheaper half to keep local:
it runs once per manual, not once per question.

Once no local model is needed at all, the `ollama` service can be removed from
`docker-compose.yml` along with the `depends_on` block in `app`.

Check it took effect:

```bash
curl -s https://<APP_DOMAIN>/healthz | python3 -m json.tool
```

## Run the evaluation

`eval/questions.csv` holds the questions to score. It ships with five example
rows and the `expected_pages` column **empty**, because the right pages depend on
the manuals you upload and are not something to guess.

Fill it in:

| column | meaning |
| --- | --- |
| `question` | what a colleague would type |
| `language` | `en` or `fi`; detected if left blank |
| `expected_doc` | the manual title as entered in the admin page, or blank for any |
| `expected_pages` | the PDF page numbers that answer it, comma separated, `12,14` or `12-14` |
| `notes` | free text, not used in scoring |

`expected_pages` is the **PDF page number**, the one the viewer shows, not the
printed page label. Both appear on every section card, so the easiest way to
fill the column is to ask the question in the app and read the number off the
card that has the answer.

Then:

```bash
python -m app.eval                                   # without Docker
docker compose exec app python -m app.eval           # with Docker
```

It runs each question through search only, with no model call, and prints the
share of questions with an expected page in the top 3 and the mean reciprocal
rank over the top 10, per language and overall. Rows with no `expected_pages`
are listed with their top result so the column can be filled in, and are left
out of the figures. Full results go to `eval/results-<date>.csv`.

Run it after every change to the manuals or the retrieval settings in
`config.yaml`, and keep the dated result files to see which way the numbers move.

## Run the tests

```bash
pip install -r requirements.txt
python -m pytest                     # about 160 tests, roughly 80 seconds
python -m pytest tests/test_ingest.py -v
```

The tests generate their own PDFs with PyMuPDF, with bookmarks, a repeated
footer, roman and arabic page labels, a bordered table, a contents page and an
index page. No real manual is read by the test suite, and none is needed to run
it. The model is a stub HTTP server started by the tests, so no Ollama is
needed either.

## Back up the data volume

Everything worth keeping is on the `app_data` volume: the SQLite database, the
uploaded manuals, the embedding files, the page cache and `users.yaml`. The
models on `ollama_models` are downloads and need no backup.

Back up:

```bash
docker run --rm \
  -v manual-assistant_app_data:/data:ro \
  -v "$PWD":/backup \
  alpine tar czf "/backup/manual-assistant-$(date +%F).tar.gz" -C /data .
```

Check the volume name first with `docker volume ls`; Compose prefixes it with
the project directory name.

Restore into an empty volume:

```bash
docker compose down
docker volume rm manual-assistant_app_data
docker volume create manual-assistant_app_data
docker run --rm \
  -v manual-assistant_app_data:/data \
  -v "$PWD":/backup \
  alpine tar xzf /backup/manual-assistant-2026-01-31.tar.gz -C /data
docker compose up -d
```

The database is fine to copy while the app is running, because SQLite is in WAL
mode, but a backup taken during an upload may catch a half-written manual. For a
clean copy, `docker compose stop app` first.

Schedule it with cron, keeping a month:

```cron
17 2 * * * cd /srv/manual-assistant && docker run --rm -v manual-assistant_app_data:/data:ro -v /srv/backups:/backup alpine tar czf /backup/ma-$(date +\%F).tar.gz -C /data . && find /srv/backups -name 'ma-*.tar.gz' -mtime +30 -delete
```

Without Docker, the same data is the `data/` directory and `users.yaml`.

## Configuration reference

**`config.yaml`** holds everything a user sees and the retrieval settings: app
name, product list, disclaimer, greeting, the three example questions, the
system prompt, chunk size and overlap, how many sections to show, the token
budget, the Reciprocal Rank Fusion constant, how much the section path is
weighted, and the boilerplate threshold. No product name, greeting, example
question or prompt text is in the Python or the HTML. Edit the file and restart.

**Environment variables** are documented in `.env.example`, which is the
reference for them. The ones worth knowing:

| Variable | Default | Notes |
| --- | --- | --- |
| `SESSION_SECRET` | none | required; signs session cookies |
| `HOST` | `127.0.0.1` | the container sets `0.0.0.0`, reached only via Caddy |
| `LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible endpoint |
| `LLM_API_KEY` | `not-needed-for-ollama` | sent as a bearer token when set |
| `LLM_MODEL` | `llama3.1:8b` | |
| `EMBED_MODEL` | `bge-m3` | multilingual, which is what makes Finnish work |
| `EMBED_BASE_URL` | the LLM host without `/v1` | Ollama's own embeddings API |
| `USERS_FILE` | `./users.yaml` | `/data/users.yaml` in the container |
| `USERS_YAML` | empty | inline users; takes precedence over the file |
| `COOKIE_SECURE` | `0` | set to `1` behind HTTPS; the container sets `1` |
| `RETENTION_DAYS` | `90` | question log retention; `0` keeps everything |
| `RATE_LIMIT_PER_MINUTE` | `20` | questions per user per minute |
| `MAX_UPLOAD_MB` | `200` | |
| `MAX_JSON_BYTES` | `16384` | JSON request body cap |
| `LOG_LEVEL` | `INFO` | logs are JSON, one object per line, on stdout |

## How it works

**Ingestion.** PyMuPDF reads each page. Every page records its 1-based PDF page
number and its printed label from `page.get_label()`, falling back to the PDF
page number. Section paths come from the PDF bookmarks, `doc.get_toc()`; a PDF
with no bookmarks falls back to detecting headings by font size, bold flags and
numbered forms such as `3.2.1 Title`. Any line whose digits normalise to a form
appearing on more than 20% of the pages, and on at least three of them, is
dropped as a running header, footer or licence stamp. Contents pages are
recognised by their dot leaders and index pages by their trailing page
references, and both are skipped. Tables are found with `page.find_tables()` and
each becomes a single chunk, in Markdown, with its header row and caption, never
split. Text is otherwise split by section at about 1,200 characters with 150
characters of overlap, never across a section boundary. Indexing is cached on
the SHA-256 of the file content.

**Search.** BM25 over tokens reduced to both their English stem and their
Finnish lemma, with the section path indexed twice. Cosine similarity over
`bge-m3` vectors held as one numpy file per document. The rankings are combined
with Reciprocal Rank Fusion at k=60. A non-English question contributes a second
pair of rankings from a short English query written by the model. Results are
reduced to one card per manual page. Whole chunks go to the model within a token
budget of about 3,000 tokens; a chunk that does not fit is left out rather than
cut.

**Answering.** The system prompt in `config.yaml` restricts the model to the
numbered sources, requires a `[n]` after every step, requires command, tab,
menu and dialog names to be copied exactly as the sources write them, and
requires `Not found in the uploaded manuals` when the sources do not answer.
Temperature 0.1, 700 output tokens, streamed.

**Storage.** SQLite holds documents, chunks, the question log and feedback.
Embeddings are numpy files on disk. Manuals are PDFs on disk. Nothing else.

**Security.** Login is enforced by middleware on every page and API call, with
`/healthz` as the one exception so the container healthcheck can run before
anyone signs in; it returns status flags only. Sessions are signed cookies with
an expiry, and the role is re-read from the user list on every request. JSON
bodies are capped at 16 kB, questions are rate limited per user, and every model
call has a timeout. `/files` serves only documents in the library and only from
the manuals directory. All text from a manual or from the model is inserted into
the page as text, never as HTML.

`app/auth.py` keeps all of this behind one interface, so company single sign-on
can replace it: write a second backend with `authenticate` and `get_user`, add a
route completing the OpenID Connect code flow against Microsoft Entra ID, and
call `issue_session` with the user it returns. No route and no page changes.

## Troubleshooting

**The page says keyword search only was used.** The embedding model is not
reachable. Check `curl -s http://<host>/healthz`, and `docker compose logs
ollama`. Search still works, it is just less good at Finnish. After fixing it,
re-index each manual so its vectors are built: **Re-index** on the admin page.

**The answer says the model could not be reached.** The language model is down
or still downloading. `docker compose logs ollama` and `docker compose ps`. The
sections are still correct.

**A manual shows `failed`.** The error is on the admin page next to it. Usual
causes: a scanned PDF with no text layer, which needs OCR before it is useful
here, a password-protected file, or a truncated upload. Other manuals are
unaffected.

**Caddy cannot get a certificate.** The domain must resolve to this machine
before Caddy starts, and ports 80 and 443 must be open. `dig +short <domain>`
and `docker compose logs caddy`.

**Nobody can sign in.** The app logs `no_users_configured` at start-up when the
user list is empty or unreadable. Check `docker compose logs app | grep users`,
then the file at `/data/users.yaml` or the `USERS_YAML` value. The hash must be
quoted, because it contains `$`.

**Answers are slow.** `llama3.1:8b` on a processor alone takes tens of seconds
for a long answer. The section list does not wait for the model and should
appear in about a second. For faster answers, use a GPU (uncomment the block in
`docker-compose.yml`) or point `LLM_BASE_URL` at a cloud provider.

**The first question after a restart is slow.** It should not be: the
lemmatiser dictionaries are loaded at start-up for this reason. If it is, check
that the start-up finished before the question arrived.
