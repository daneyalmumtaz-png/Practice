# Showing this in a meeting, from your own machine

Running it on your work laptop for a demonstration is the right call. It needs
no server, no IT ticket and no deployment decision, and nothing is exposed to
the network: the app binds to `127.0.0.1`, which means that computer only.

Two things will ruin the demonstration if you do not plan for them, and neither
is a bug.

## The two real risks

**1. The written answer is slow.** `llama3.1:8b` on a laptop with no graphics
card takes 20 to 60 seconds. In a meeting that is an unbearable silence, and
people will remember the waiting rather than the tool.

The app is built for this: the matching sections appear in about one second, and
the written answer streams in underneath while you keep talking. Use that.
Demonstrate the sections, speak over the answer as it writes itself, and say out
loud that the prose takes a server or a hosted model to be quick. Nobody minds a
stated limitation. They mind a frozen screen.

**2. Nothing is ready on the day if you start on the day.** The models are about
6 GB and the first indexing of a real manual takes several minutes. Do the whole
setup, with the real manual loaded, **at least a day before**, and then run
through your questions once on the morning of the meeting.

## The day before

Follow `INSTALL - READ ME FIRST.txt`, which is the plain-English version, or in
short:

1. Install Python 3.12 or newer, ticking **"Add python.exe to PATH"**.
2. Install Ollama, then `ollama pull llama3.1:8b` and `ollama pull bge-m3`.
3. Double-click `windows\Setup Manual Assistant.bat`, choose a username and
   password, and write them down.
4. Double-click `windows\Start Manual Assistant.bat`.
5. Sign in, open **Admin**, and upload **a real manual**. This matters. A demo
   on the generated example document proves nothing to an engineer; a demo on
   the OpenRoads Designer help they fought with last week proves everything.
6. Remove the example document so it cannot appear in a result during the
   meeting:

   ```
   .venv\Scripts\python.exe scripts\demo_manual.py --remove
   ```

Then check all three of these, because each is a thing that can embarrass you:

```
curl http://127.0.0.1:8000/healthz
```

Use whatever address the start window printed: setup moves to port 8099 if 8000
is already taken on your machine.

- `"status": "ok"` means the app is fine.
- `"model": {"ok": true}` means the answer will be written. If this is false,
  Ollama is not running and you will demo silence.
- `"embeddings": {"ok": true}` means Finnish questions will work. If this is
  false, run `ollama pull bge-m3` and then press **Re-index** on the Admin page,
  or the Finnish part of your demo falls flat.

### If your laptop cannot take the installers

A managed Destia laptop may refuse them. Python and Ollama both install per
user and normally need no administrator rights, but policy may still block them.
Do not spend the day before the meeting fighting it. Either request them through
the normal software route with time to spare, or demonstrate without Ollama: the
section search still works and is the more convincing half anyway. Say that the
written answer needs a model that is not installed yet.

## Choosing your questions

Pick four, written down in advance, in this order. Do not improvise in the room.

1. **A procedure question in English** whose answer you already know, so you can
   confirm out loud that the cited page is the right page. This is the whole
   point of the tool and the only claim that matters.
2. **The same question in Finnish.** This is the moment that lands with a
   Finnish audience: the manual is in English, the question is not, and it still
   finds the right section. Say that out loud, because nobody will notice
   otherwise.
3. **A follow-up**, two or three words, such as "and the fields?" after the
   first question. It gets rewritten into a standalone question before
   searching, which looks like the tool is paying attention.
4. **A question the manual does not answer.** Ask it deliberately. It replies
   "Not found in the uploaded manuals" and still shows the closest sections.
   Demonstrating that it refuses to invent an answer buys you more credibility
   than three correct answers, especially with anyone in the room who distrusts
   this kind of tool.

Run all four the morning of the meeting. If one of them answers badly, replace
the question rather than hoping.

## In the room, in order

1. **The section cards first.** Manual title, section path, printed page label
   and PDF page number, side by side. Point out that the printed page and the
   PDF page disagree, which is exactly why looking things up by hand is painful.
2. **Click "Open page".** The PDF opens at that page. This is the moment people
   understand what it is for.
3. **Click a `[1]` in the answer.** It scrolls to the card it came from. Say
   that every step has to cite a section, and the model is only given those
   sections to work from.
4. **Then the Finnish question**, then the follow-up, then the one it cannot
   answer.

Say the three honest things, early rather than when challenged:

- It is a prototype, written for our team, not an IT service.
- Trust the cited page, not the prose. The page is retrieved; the prose is
  written by a model and can be wrong.
- Nothing leaves the machine. The manuals and the model are both local.

## Do not

- **Do not demo over Teams screen share without testing it first.** Streaming
  text over a compressed share can look like a hang.
- **Do not promise a date.** The deployment needs a server, a licence
  confirmation and an IT review; `DEPLOY.md` and `IT-REQUEST.md` cover those.
  Ask for permission to pilot, not for a launch.
- **Do not open it to the network** to let someone try it from their own laptop.
  That is a deployment, with all the obligations in `DEPLOY.md`, not a demo.
  Hand them your keyboard instead.
- **Do not upload a manual you are not sure we are licensed to use** if anyone
  in the room would have to answer for it. For a demo on your own machine with a
  manual you already have, this is not an issue; it becomes one the moment it
  goes on a server.

## Afterwards

Ask for two things while the room is still interested: a named group of five to
ten people for a pilot, and someone to confirm the Bentley licence position.
Those are the two items that unblock everything else, and they are decisions, not
work.
