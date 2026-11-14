# Deploying Manual Assistant for Destia

This is the runbook for putting the app on a server that colleagues reach in a
browser. The README covers running it on one machine; this covers running it for
other people, which is a different job with different obligations.

Read [Before you start](#before-you-start) first. Three of those items are not
technical and will take longer than the install.

- [Before you start](#before-you-start)
- [The shape of the deployment](#the-shape-of-the-deployment)
- [Azure, and what about Cloudflare](#azure-and-what-about-cloudflare)
- [Phase 1: pilot, about half a day](#phase-1-pilot-about-half-a-day)
- [Phase 2: production](#phase-2-production)
- [Running it day to day](#running-it-day-to-day)
- [What this prototype does not yet do](#what-this-prototype-does-not-yet-do)

## Before you start

You cannot complete this alone, and you should not try. Four things need someone
else's decision.

**1. The Bentley licence.** The app stores manual PDFs on a server and serves
pages of them to colleagues. That is redistribution inside the company. It is
probably covered by Destia's subscription, since every reader is a licensed
user, but probably is not good enough for a system that will sit there for
years. Get it in writing from whoever owns the Bentley contract, or from
Bentley's account team. The upload form already records who confirmed the
licence for each document and when, so the evidence trail exists once someone
makes the call.

**2. A server, from Destia IT.** Running company data on a machine you rented
personally is not an option, however convenient. Ask IT. `IT-REQUEST.md` next to
this file is written to be forwarded to them as it stands.

**3. Personal data.** The question log stores a username, a timestamp and the
question text for 90 days by default. That is personal data under GDPR and needs
a line in Destia's processing register, plus a sentence somewhere colleagues can
see it, saying their questions are logged and for how long. Set
`RETENTION_DAYS` to whatever your data protection officer asks for; `0` keeps
everything, which is almost certainly the wrong answer.

**4. An IT security review.** The app will have a login page on the public
internet. It was built for that (see the Security section of the README) but it
has not been penetration tested and it is a prototype written for one team. Say
so plainly when you ask. The honest framing is a pilot with a named group, not a
company service.

None of this is a reason not to do it. It is the order to do it in.

## The shape of the deployment

```
   colleague's browser
          |
          |  HTTPS, port 443 only
          v
   +---------------+
   |     caddy     |  certificate, reverse proxy. The only published ports.
   +---------------+
          |
   +---------------+     +-----------------+
   |      app      |---->|     ollama      |  or a cloud model, see Phase 2
   |  (this app)   |     |  no open ports  |
   +---------------+     +-----------------+
          |
   +---------------+
   |  app_data     |  manuals, index, database, users
   |  volume       |  this is the thing to back up
   +---------------+
```

One virtual machine runs all three containers through `docker compose`. Nothing
but Caddy listens on a public port. The app's own login stands between the
internet and every page.

**Machine size.** Two sensible choices, and the model decides which.

| | Running Ollama on the machine | Using a cloud model |
| --- | --- | --- |
| vCPU, RAM | 4 vCPU, 16 GB | 2 vCPU, 8 GB |
| Disk | 100 GB | 60 GB |
| Answer speed | 20 to 60 s on CPU | 2 to 10 s |
| Manual text leaves Destia | no | yes, to the model provider |
| Monthly cost | higher machine, no usage fees | smaller machine, per-token fees |

Start with Ollama on the machine. It keeps the manuals inside the server, which
removes a whole conversation with IT, and it costs a fixed amount. Move to a
cloud model in Phase 2 if the answer speed annoys people, which it will.

Price the machine in your provider's calculator rather than trusting a figure
here; they change. Disk is sized for the models (about 6 GB), the manuals, and
the page-image cache, which grows slowly.

## Azure, and what about Cloudflare

### Azure: yes, on a virtual machine

Take `Standard_D4s_v5` or similar, 4 vCPU and 16 GB, Ubuntu 24.04, in an EU
region, with a managed disk and Azure Backup on it. Then follow Phase 1 below
unchanged: it is a plain Linux machine running `docker compose`.

**Not App Service and not Container Apps.** Both want their persistent storage
mounted from Azure Files over SMB, and Microsoft's own documentation says of
those mounts: "We don't recommend that you use storage mounts for local
databases, such as SQLite, or for any other applications and components that
rely on file handles and locks." This app's database is SQLite in WAL mode,
which needs byte-range locking that SMB does not provide. The failure is not a
clean error either: writes hang, then time out, and a database can be corrupted.
Ollama is a second reason, needing 16 GB of memory and a persistent 6 GB model
store that neither platform is shaped for.

If Destia already runs Azure OpenAI, use it for the answer model and the machine
drops to 2 vCPU and 8 GB, because nothing heavy runs locally except the
embeddings. See [Phase 2](#phase-2-production) for the one request that has to
be verified first.

### Cloudflare: not for running it, but strongly worth it in front

**Workers cannot run this app.** Python Workers are now generally available and
do run FastAPI, but they execute CPython compiled to WebAssembly under Pyodide.
PyMuPDF, which does all the PDF reading, is a binding to the MuPDF C library and
has no WebAssembly wheel. On top of that there is a 128 MB memory ceiling shared
with the runtime, threads do not work, and there is no local disk for SQLite,
the manuals or the page-image cache. It is the wrong shape of platform for this,
not a porting exercise.

**Cloudflare Tunnel and Access in front of the Azure machine is the best
available answer to two of the four obstacles above**, and it is worth putting
to IT as the preferred design.

- **Tunnel removes the inbound ports.** `cloudflared` runs on the machine and
  makes an outbound connection to Cloudflare. Nothing listens on a public port,
  no certificate challenge on port 80, and the machine needs no public IP. The
  objection to opening 80 and 443 disappears rather than being argued about.
- **Access gives Destia sign-on without the code being written.** Cloudflare
  Access sits in front of the app and authenticates against Entra ID as a SAML
  identity provider, so only signed-in Destia people reach it at all. The free
  Zero Trust plan covers up to 50 users, which is a pilot and then some; beyond
  that it is about 7 USD per user per month.

Two honest costs:

1. **The hostname has to live on a Cloudflare zone.** That means either moving
   `destia.fi` nameservers to Cloudflare, which is a large decision nobody will
   make for this app, or delegating one subdomain with an `NS` record. Ask for
   the delegation, not the domain.
2. **Colleagues would sign in twice** at first: once at Cloudflare Access with
   their Destia account, then again with their app password. Access does pass
   the authenticated identity to the origin in a signed `Cf-Access-Jwt-Assertion`
   header, so a smaller change than full OpenID Connect could read it and drop
   the second login. That change must verify the JWT signature, never trust the
   plain email header, or anyone who reaches the origin directly can claim to be
   anyone. With a tunnel there is no direct path, but defence in depth is the
   whole point of the exercise.

For the pilot, accept the double sign-in. It costs nothing to build and it is
still a better security story than a password file behind an open port. Reports
suggest the free Zero Trust checkout asks for card authorisation even at zero
cost, so have IT confirm the billing position before anyone depends on it.

## Phase 1: pilot, about half a day

Goal: five to ten named colleagues using it on a Destia subdomain, with real
manuals, so you learn whether it is worth productionising. Accounts are created
by hand. That is fine for ten people and wrong for a hundred.

### Step 1. Get the machine and the name

Ask IT for, and write down:

- A Linux virtual machine, Ubuntu 24.04 LTS, in an EU region. They will have a
  standard one; take it.
- 4 vCPU, 16 GB RAM, 100 GB disk.
- A DNS name, for example `kasikirjat.destia.fi` or
  `manual-assistant.destia.fi`, with an `A` record pointing at the machine's
  public IP.
- Inbound TCP 80 and 443 open to the internet, and 22 open to wherever you
  administer from.

**Why 80 and 443 are public.** Caddy proves it controls the name by answering a
challenge on port 80, so that port has to be reachable from the internet for a
certificate to issue and renew. Access control is the app's login, not the
firewall. If IT will not accept that, the alternative is a DNS-01 challenge
against Destia's DNS provider, which needs a Caddy build with that provider's
plugin. Say so before you design around it; do not quietly serve the app over
plain HTTP instead.

Check the name resolves before going further, because nothing works until it
does:

```bash
dig +short kasikirjat.destia.fi
```

### Step 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
docker --version && docker compose version
```

### Step 3. Put the app on the machine

```bash
sudo mkdir -p /srv && sudo chown "$USER" /srv
cd /srv
git clone https://github.com/daneyalmumtaz-png/Practice.git manual-assistant-repo
cd manual-assistant-repo/manual-assistant
```

If the repository is not reachable from the server, copy the ZIP up instead:

```bash
scp manual-assistant.zip user@server:/srv/
ssh user@server 'cd /srv && unzip manual-assistant.zip'
```

### Step 4. Configure it

```bash
cp .env.example .env
nano .env
```

Set these four:

```bash
APP_DOMAIN=kasikirjat.destia.fi
SESSION_SECRET=            # paste the output of the command below
COOKIE_SECURE=1            # the default; it must stay 1 behind HTTPS
RETENTION_DAYS=90          # or whatever your data protection officer says
```

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

`SESSION_SECRET` signs the session cookies. Keep it out of git and out of chat
messages. Changing it signs everyone out, which is also how you sign everyone
out in a hurry.

Leave the model settings alone for now. Blank means the `ollama` service.

### Step 5. Start it

```bash
docker compose up -d --build
docker compose logs -f ollama
```

The first start downloads `llama3.1:8b` and `bge-m3`, about 6 GB. The `app`
service waits until both are present, so `docker compose ps` will show it
starting for a while. That is correct behaviour, not a hang.

When `app` reads healthy:

```bash
docker compose ps
curl -s https://kasikirjat.destia.fi/healthz | python3 -m json.tool
```

You want `"status": "ok"` and `"ok": true` for both `model` and `embeddings`. If
`embeddings` is false the app still works, but Finnish questions will be much
worse, so fix it before letting anyone in.

### Step 6. Create the accounts

Generate a hash per person:

```bash
docker compose exec app python -m app.auth hash
```

Then write the file on the data volume, so it survives rebuilds. The `-T` is
not optional: without it `exec` allocates a terminal, which can put a carriage
return inside a hash, and then the password silently stops working.

```bash
docker compose exec -T app sh -c 'cat > /data/users.yaml' <<'YAML'
users:
  - username: dmumtaz
    role: admin
    password_hash: "$2b$12$..."
  - username: colleague.one
    role: user
    password_hash: "$2b$12$..."
YAML
docker compose restart app
```

Give `admin` only to the people who should be able to upload and delete
manuals. Everyone else gets `user`. The role is re-read on every request, so
demoting someone takes effect immediately rather than when their cookie expires.

Send each person their password through whatever Destia uses for secrets, not
in an email body and not in Teams chat.

### Step 7. Load the manuals

Sign in, open **Admin**, and upload. For each document set the title, product
and version, tick the licence confirmation, and leave the status on `current`.
When a new edition arrives, upload it and set the old one to `superseded`: it
stays searchable for admins and is never cited in an answer again.

For a batch, the command line is less tedious:

```bash
docker compose cp handbook.pdf app:/data/manuals/handbook.pdf
docker compose exec app python -m app.ingest /data/manuals/handbook.pdf \
  --title "OpenRoads Designer Help" --product "OpenRoads Designer" \
  --version "2024.1" --uploaded-by dmumtaz --licence-confirmed-by dmumtaz
```

A long manual takes a few minutes, most of it building the index. A PDF that
cannot be read shows `failed` with the reason on the Admin page, and every other
manual keeps working.

### Step 8. Check it before telling anyone about it

Run these five, and do not announce the app until all five pass.

```bash
# 1. The app is sound.
docker compose exec app python -m pytest -q          # expect 160 passed

# 2. Nothing is exposed but Caddy.
docker compose ps                                     # only caddy shows ports

# 3. A page refuses to load without signing in.
curl -s -o /dev/null -w '%{http_code}\n' https://kasikirjat.destia.fi/
# expect 303, a redirect to the sign-in page, not 200

# 4. The certificate is real and trusted.
curl -sI https://kasikirjat.destia.fi/login | head -1

# 5. A backup restores. Do this now, not after you need it.
```

Then sign in yourself and ask five questions you have actually been asked by
colleagues. Check that the cited page is the right page. That is the only test
that matters, and no amount of green ticks replaces it.

### Step 9. Set up the backup before you hand it over

```bash
docker volume ls | grep app_data        # note the exact name
mkdir -p /srv/backups
```

```cron
17 2 * * * cd /srv/manual-assistant-repo/manual-assistant && docker run --rm -v manual-assistant_app_data:/data:ro -v /srv/backups:/backup alpine tar czf /backup/ma-$(date +\%F).tar.gz -C /data . && find /srv/backups -name 'ma-*.tar.gz' -mtime +30 -delete
```

Then restore one into a scratch volume and confirm the database and the manuals
are in it. A backup you have never restored is not a backup. The README's
**Back up the data volume** section has the restore commands.

## Phase 2: production

Do this only if the pilot earns it. Two changes, in this order.

### Sign-in with Destia accounts

This is the one that decides whether the app can grow past the pilot. Hand-made
passwords in `users.yaml` do not scale, cannot be revoked centrally, and will
not survive an IT review.

`app/auth.py` was written for this: everything the rest of the app uses is the
`AuthBackend` interface, `User`, and three dependencies. Replacing it means
writing a second backend with `authenticate` and `get_user`, adding a route that
completes the OpenID Connect code flow against Microsoft Entra ID, and calling
`issue_session` with the user it returns. No route and no page changes. Map an
Entra group to the `admin` role so IT manages who can upload.

This is real work, maybe two days including the app registration in Entra and
testing, and it is not written yet. It is the right next thing to build.

### A faster model

`llama3.1:8b` on CPU takes 20 to 60 seconds to write an answer. The section
cards appear in about a second either way, so the app is usable, but people will
complain about the prose.

Three variables switch providers, no code change:

```bash
LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1
LLM_API_KEY=<key>
LLM_MODEL=<your deployment name>
```

Azure OpenAI is the obvious choice if Destia is a Microsoft shop: the data stays
in Destia's own tenant, which is the argument that wins with IT. Its newer
`/openai/v1/` route takes `Authorization: Bearer`, which is exactly what this
app sends, so it should work unchanged.

**Verify that before planning around it.** Microsoft's own examples pass an
Entra token as the bearer value, not a resource API key, and older Azure OpenAI
routes want an `api-key` header instead. Once IT provisions the resource, one
request settles it:

```bash
curl -sS https://<resource>.openai.azure.com/openai/v1/chat/completions \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"model":"<deployment>","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
```

If that returns a completion, set the three variables and restart `app`. If it
returns 401, the app needs a small change in `app/llm.py` to send the `api-key`
header, or to fetch an Entra token. Either is an hour's work; do not start it
before the curl has told you which.

Keep embeddings on the local Ollama. They run once per manual rather than once
per question, so they cost almost nothing to keep in-house, and `bge-m3` is
what the Finnish retrieval was built and tested against. Note that
`EMBED_BASE_URL` expects Ollama's own embeddings API, not an OpenAI-shaped one,
so pointing it at a cloud provider needs a code change as well.

Once no local chat model is needed, the `ollama` service can shrink or, if
embeddings also move, be removed from `docker-compose.yml` along with the
`depends_on` block in `app`.

## Running it day to day

**Updating.** The data volume survives a rebuild.

```bash
cd /srv/manual-assistant-repo && git pull
cd manual-assistant && docker compose up -d --build
docker compose exec app python -m pytest -q
curl -s https://kasikirjat.destia.fi/healthz | python3 -m json.tool
```

**Logs.** JSON, one object per line, on stdout.

```bash
docker compose logs --tail 200 app
docker compose logs app | grep '"level": "ERROR"'
docker compose logs app | grep '"msg": "login_failed"'      # watch this one
```

**Is anyone using it.** The question log answers honestly, which a dashboard
would not.

```bash
docker compose exec app sqlite3 /data/app.db \
  "SELECT date(asked_at) d, COUNT(*), COUNT(DISTINCT username) FROM questions GROUP BY d ORDER BY d DESC LIMIT 14;"
```

**Is it any good.** Fill in `expected_pages` in `eval/questions.csv` from the
card numbers for questions you know the answer to, then:

```bash
docker compose exec app python -m app.eval
```

Keep the dated result files. They are the only way to tell whether a change to
`config.yaml` helped or hurt.

**Reading feedback.** The thumbs under each answer go into the database.

```bash
docker compose exec app sqlite3 /data/app.db \
  "SELECT f.rating, f.comment, q.question FROM feedback f JOIN questions q ON q.id=f.question_id ORDER BY f.id DESC LIMIT 20;"
```

Every thumbs-down with a comment is a retrieval bug report. Work through them.

**Someone leaves.** Remove their block from `/data/users.yaml` and restart
`app`. Their next request is refused, cookie or no cookie.

## What this prototype does not yet do

State these when you ask for approval. They are the gap between this and a
service.

- **No single sign-on.** Accounts are a YAML file. Phase 2 above.
- **One process, one machine.** The rate limit is in memory and the database is
  SQLite, so a second app replica would not share either. Fine for one team.
- **No penetration test.** Login, session signing, body limits, rate limiting,
  path handling and output escaping are all implemented and covered by tests,
  but no outside party has attacked it.
- **`/healthz` needs no login.** The one deliberate exception, so the container
  healthcheck can run before anyone signs in. It returns status flags only, no
  configuration. Put it behind a token if IT asks.
- **Scanned manuals do not work.** A PDF with no text layer indexes as `failed`.
  It would need OCR first, which the app does not do.
- **Answer quality against real Bentley manuals is unmeasured.** The retrieval
  was built and tested against generated documents. Whether the model copies
  command and ribbon names exactly, as the prompt instructs, is the first thing
  to judge with real manuals in front of you. Trust the section cards, which
  link to the page, over the prose.
