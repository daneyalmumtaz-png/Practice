# Request to IT: server for an internal manual search tool

Forward this as it stands, or paste it into a ticket. Replace the bracketed
items before sending. Everything else is written to answer the questions an IT
team asks first.

---

**To:** Destia IT infrastructure and information security
**From:** [your name], Geotechnical Design
**Subject:** Virtual machine request, internal manual search tool, pilot with
[number] users

## What I am asking for

A small Linux virtual machine and a DNS name, so that a group of designers can
search our Bentley software manuals in a browser instead of scrolling through
PDFs.

| Item | Request |
| --- | --- |
| Operating system | Ubuntu 24.04 LTS |
| Size | 4 vCPU, 16 GB RAM, 100 GB disk |
| Region | Any EU region that fits our standard |
| DNS name | `[kasikirjat].destia.fi`, `A` record to the machine's public IP |
| Inbound firewall | TCP 443 and 80 from the internet, TCP 22 from our admin range |
| Outbound | HTTPS for operating system updates, Docker images and the model download |
| Backup | Nightly file backup of one Docker volume, 30 days retention |
| Administrators | [your name], plus whoever IT nominates |

I will install and run the application. I am not asking IT to operate it.

## What it does

Designers upload PDF manuals for the software we use, mainly Bentley OpenRoads
Designer and OpenGround. The tool indexes each manual by section and page, and
answers a typed question in English or Finnish by listing the matching manual
sections, each linking to the exact page of the PDF, followed by a short written
answer in which every step cites one of those sections.

The written answer is produced by a language model that runs **on this virtual
machine**, not by an external service. No manual text and no question text
leaves the machine. If we later move to a hosted model, that is a separate
request and I will raise it as one.

It replaces people emailing each other screenshots of manual pages.

## Why the ports are open

Port 443 serves the application. Port 80 exists because the certificate
authority proves we control the DNS name by requesting a file over port 80, and
it has to stay reachable for the certificate to renew automatically.

Access control is the application's own login, not the firewall. Every page and
every API call requires a signed-in session; there is no anonymous page except a
health check that returns status flags and no configuration.

If network-level restriction is required instead, the alternative is a
DNS-01 certificate challenge against our DNS provider, which needs a different
build of the proxy. Tell me and I will do that instead of opening port 80.

## Security

Implemented and covered by automated tests:

- Login required for every page and every API call, enforced in middleware.
- Passwords stored as bcrypt hashes. Sessions are signed cookies, HttpOnly,
  SameSite, Secure, with an expiry. The user's role is re-read from the account
  list on every request, so removing someone takes effect on their next request
  rather than when their cookie expires.
- Two roles. A normal user can only ask questions. Only an administrator can
  upload, re-index or delete a manual.
- Request body limit of 16 kB on the JSON endpoints, 200 MB on uploads, and a
  rate limit of 20 questions per user per minute.
- Uploads are validated by file content, not by the file extension, and the
  stored file name is sanitised. The file-serving endpoint will only serve
  documents in its own library from its own directory.
- All text taken from a manual or produced by the model is inserted into the
  page as text, never as markup.
- Only the reverse proxy publishes a port. The application and the model are
  reachable only on the internal container network.
- Structured JSON logs to standard output, including every failed sign-in.

Not done, stated plainly:

- **No single sign-on yet.** For the pilot, accounts are a file of usernames,
  bcrypt hashes and roles that I maintain. The authentication code was written
  behind one interface so that Entra ID can replace it, and I intend to build
  that if the pilot is worth continuing. If IT requires SSO before any
  deployment, say so and I will build it first.
- **No penetration test.** This is software I had built for our team. The
  controls above are implemented and tested, but no third party has attacked it.
  I am proposing a pilot with named users, not a company service.
- **One machine, one process.** The rate limit is held in memory and the
  database is SQLite, so this does not scale horizontally. That is appropriate
  for one team and would need revisiting for wider use.

## Personal data

The application keeps a log of questions: username, timestamp, question text,
which manual sections were used, how long the answer took, and any thumbs
up or down. It is used to see whether the tool is useful and where its answers
are wrong.

- Retention is a setting. The default is 90 days, after which entries are
  deleted automatically by a daily task. Tell me the number you want.
- No manual content and no question text is sent outside the machine.
- This needs an entry in our processing register and a sentence shown to users.
  I will draft both if you tell me the format.

Please involve the data protection officer if that is the normal route.

## Licensing

The manuals are Bentley documentation covered by our own subscription. Storing
them on an internal server and showing pages of them to licensed Destia
employees is, as far as I can tell, within our licence, but I am not the right
person to decide that. I am asking the owner of the Bentley contract in
parallel, and I will not load any manual until that is confirmed.

The upload form requires the uploader to confirm, per document, that Destia is
licensed to use it in this tool, and records who confirmed it and when. So the
evidence trail exists.

## Cost

The virtual machine and its backup. No licence fee: the application and both
models are open source and run locally. No per-question cost.

If we later move the answer model to Azure OpenAI for speed, that adds a
per-token cost and I will bring a figure before doing it.

## What I need to proceed

1. The virtual machine, the DNS name and the firewall rules above.
2. A nightly backup of one Docker volume on that machine.
3. Confirmation on the personal data retention period.
4. Your position on single sign-on: acceptable to pilot without it, or build it
   first.

Happy to walk through the application with you before anything is installed.

---

## Notes for yourself, not for IT

Delete this section before sending.

- Decide the DNS name before you send this. `kasikirjat.destia.fi` is readable
  Finnish and will not need explaining; an English name will get renamed later.
- Name the pilot group in the subject line. A request for "a server" gets
  queued; a request for "a pilot with eight named designers in geotechnical
  design" gets a decision.
- If IT says SSO first, that is a reasonable answer and not a refusal. It is
  roughly two days of work and it is in `DEPLOY.md` as Phase 2.
- Ask about the Bentley licence **in parallel**, not after. It is the item most
  likely to take weeks, and it is the one that can stop the whole thing.
- If IT offers Azure App Service or a container platform instead of a virtual
  machine, take the virtual machine for the pilot. The app needs persistent
  local disk for the database, the manuals and the model, and a plain machine
  running `docker compose` is the shortest path to something working. Move it
  later if it earns the attention.
