/* Manual Assistant, chat page.
 *
 * Nothing from the server or from the model is ever assigned to innerHTML.
 * Text goes in through textContent and attributes through setAttribute, so a
 * manual containing angle brackets, or a model that emits a tag, cannot put
 * markup into this page.
 *
 * Order on screen, which is the order the user needs: the governing sections
 * first, from /api/search, then the streamed answer below them.
 */
(function () {
  "use strict";

  var cfg = window.APP_CONFIG || {};
  var conversation = document.getElementById("conversation");
  var form = document.getElementById("askform");
  var input = document.getElementById("question");
  var submit = document.getElementById("asksubmit");
  var history = [];
  var busy = false;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  /* ----------------------------------------------------------------- header */

  document.title = cfg.appName || "Manual Assistant";
  document.getElementById("app-name").textContent = cfg.appName || "";
  document.getElementById("disclaimer").textContent = cfg.disclaimer || "";
  document.getElementById("greeting").textContent = cfg.greeting || "";

  var who = document.getElementById("who");
  if (cfg.user) {
    who.appendChild(el("span", null, cfg.user.username + " "));
    if (cfg.user.role === "admin") {
      var adminLink = el("a", null, "Admin");
      adminLink.setAttribute("href", "/admin");
      who.appendChild(adminLink);
      who.appendChild(el("span", null, " "));
    }
    var logout = document.createElement("form");
    logout.setAttribute("method", "post");
    logout.setAttribute("action", "/logout");
    var logoutButton = el("button", null, "Sign out");
    logoutButton.setAttribute("type", "submit");
    logout.appendChild(logoutButton);
    who.appendChild(logout);
  }

  var examples = document.getElementById("examples");
  (cfg.examples || []).forEach(function (text) {
    var button = el("button", null, text);
    button.setAttribute("type", "button");
    button.addEventListener("click", function () {
      if (!busy) { ask(text); }
    });
    examples.appendChild(button);
  });

  /* ------------------------------------------------------------ highlighting */

  /* Wrap each matched word in <mark>. Built from text nodes, so the snippet
     itself is never parsed as HTML. */
  function snippetNode(text, words, isTable) {
    var node = el("div", isTable ? "snippet table" : "snippet");
    if (!words || !words.length) {
      node.textContent = text;
      return node;
    }
    var sorted = words.slice().sort(function (a, b) { return b.length - a.length; });
    var lower = text.toLowerCase();
    var marks = new Array(text.length);
    sorted.forEach(function (word) {
      if (!word) { return; }
      var needle = word.toLowerCase();
      var from = 0;
      while (true) {
        var at = lower.indexOf(needle, from);
        if (at === -1) { break; }
        var beforeOk = at === 0 || !/[0-9a-zà-öø-ÿ]/i.test(text[at - 1]);
        var afterIndex = at + needle.length;
        var afterOk = afterIndex >= text.length || !/[0-9a-zà-öø-ÿ]/i.test(text[afterIndex]);
        if (beforeOk && afterOk) {
          for (var i = at; i < afterIndex; i++) { marks[i] = true; }
        }
        from = at + needle.length;
      }
    });
    var buffer = "";
    var marked = false;
    function flush() {
      if (!buffer) { return; }
      if (marked) {
        node.appendChild(el("mark", null, buffer));
      } else {
        node.appendChild(document.createTextNode(buffer));
      }
      buffer = "";
    }
    for (var j = 0; j < text.length; j++) {
      var isMark = !!marks[j];
      if (isMark !== marked) { flush(); marked = isMark; }
      buffer += text[j];
    }
    flush();
    return node;
  }

  /* ------------------------------------------------------------------- cards */

  function sectionCard(section, turnId) {
    var card = el("div", "card");
    card.id = turnId + "-card-" + section.n;

    var titleRow = el("div");
    titleRow.appendChild(el("span", "num", section.n));
    var title = el("span", "title", section.doc_title);
    titleRow.appendChild(title);
    if (section.product || section.version) {
      var tag = [section.product, section.version].filter(Boolean).join(" ");
      titleRow.appendChild(document.createTextNode(" "));
      titleRow.appendChild(el("span", "badge", tag));
    }
    if (section.chunk_type === "table") {
      titleRow.appendChild(document.createTextNode(" "));
      titleRow.appendChild(el("span", "badge", "table"));
    }
    card.appendChild(titleRow);

    card.appendChild(el("p", "path", section.section_path || "No section recorded"));
    card.appendChild(el(
      "p",
      "pageline",
      "Printed page " + section.page_label + " · PDF page " + section.pdf_page
    ));
    card.appendChild(snippetNode(
      section.snippet || "", section.highlights, section.chunk_type === "table"
    ));

    var actions = el("div", "actions");
    var open = el("a", null, "Open page");
    open.setAttribute("href", section.file_url);
    open.setAttribute("target", "_blank");
    open.setAttribute("rel", "noopener");
    actions.appendChild(open);

    var previewButton = el("button", null, "Preview");
    previewButton.setAttribute("type", "button");
    var image = null;
    previewButton.addEventListener("click", function () {
      if (image) {
        var hidden = image.hasAttribute("hidden");
        if (hidden) { image.removeAttribute("hidden"); } else { image.setAttribute("hidden", ""); }
        previewButton.textContent = hidden ? "Hide preview" : "Preview";
        return;
      }
      image = document.createElement("img");
      image.className = "preview";
      image.setAttribute("loading", "lazy");
      image.setAttribute(
        "alt",
        "Page " + section.pdf_page + " of " + section.doc_title
      );
      image.setAttribute("src", section.page_image_url);
      image.addEventListener("error", function () {
        image.remove();
        image = null;
        previewButton.textContent = "Preview unavailable";
        previewButton.disabled = true;
      });
      card.appendChild(image);
      previewButton.textContent = "Hide preview";
    });
    actions.appendChild(previewButton);
    card.appendChild(actions);
    return card;
  }

  function flash(turnId, number) {
    var card = document.getElementById(turnId + "-card-" + number);
    if (!card) { return; }
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("flash");
    window.setTimeout(function () { card.classList.remove("flash"); }, 1600);
  }

  /* ---------------------------------------------------------------- markdown */

  /* The only formatting the model is allowed to produce: numbered steps, bullet
     points, paragraphs, bold, and [n] citations. Everything else is plain text. */
  function inlineInto(parent, text, turnId) {
    var pattern = /\[(\d{1,2})\]|\*\*([^*]+)\*\*/g;
    var at = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > at) {
        parent.appendChild(document.createTextNode(text.slice(at, match.index)));
      }
      if (match[1]) {
        var number = match[1];
        var cite = el("a", "cite", "[" + number + "]");
        cite.setAttribute("href", "#" + turnId + "-card-" + number);
        cite.addEventListener("click", function (event) {
          event.preventDefault();
          flash(turnId, number);
        });
        parent.appendChild(cite);
      } else {
        parent.appendChild(el("strong", null, match[2]));
      }
      at = match.index + match[0].length;
    }
    if (at < text.length) {
      parent.appendChild(document.createTextNode(text.slice(at)));
    }
  }

  function renderAnswer(container, markdown, turnId) {
    container.textContent = "";
    var list = null;
    markdown.split("\n").forEach(function (raw) {
      var line = raw.trim();
      if (!line) { list = null; return; }
      var ordered = line.match(/^(\d{1,2})[.)]\s+(.*)$/);
      var bullet = line.match(/^[-*•]\s+(.*)$/);
      if (ordered) {
        if (!list || list.tagName !== "OL") {
          list = document.createElement("ol");
          container.appendChild(list);
        }
        var oli = document.createElement("li");
        inlineInto(oli, ordered[2], turnId);
        list.appendChild(oli);
      } else if (bullet) {
        if (!list || list.tagName !== "UL") {
          list = document.createElement("ul");
          container.appendChild(list);
        }
        var uli = document.createElement("li");
        inlineInto(uli, bullet[1], turnId);
        list.appendChild(uli);
      } else {
        list = null;
        var p = document.createElement("p");
        inlineInto(p, line, turnId);
        container.appendChild(p);
      }
    });
  }

  /* ---------------------------------------------------------------- feedback */

  function ratingRow(questionId) {
    var row = el("div", "rating");
    var note = el("span", null, "");
    var buttons = {};

    function send(rating) {
      var comment = "";
      if (rating === "down") {
        comment = window.prompt("What was wrong? (optional)") || "";
      }
      fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question_id: questionId, rating: rating, comment: comment.slice(0, 2000)
        })
      }).then(function (response) {
        if (!response.ok) { throw new Error("failed"); }
        buttons.up.setAttribute("aria-pressed", String(rating === "up"));
        buttons.down.setAttribute("aria-pressed", String(rating === "down"));
        note.textContent = "Thank you.";
      }).catch(function () {
        note.textContent = "Feedback could not be saved.";
      });
    }

    ["up", "down"].forEach(function (rating) {
      var button = el("button", null, rating === "up" ? "\u{1F44D} Helpful" : "\u{1F44E} Not helpful");
      button.setAttribute("type", "button");
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", function () { send(rating); });
      buttons[rating] = button;
      row.appendChild(button);
    });
    row.appendChild(note);
    return row;
  }

  /* --------------------------------------------------------------- SSE parse */

  function handleEvents(buffer, onEvent) {
    var parts = buffer.split("\n\n");
    var remainder = parts.pop();
    parts.forEach(function (block) {
      var name = "message";
      var data = [];
      block.split("\n").forEach(function (line) {
        if (line.indexOf("event:") === 0) {
          name = line.slice(6).trim();
        } else if (line.indexOf("data:") === 0) {
          data.push(line.slice(5).trim());
        }
      });
      if (!data.length) { return; }
      try {
        onEvent(name, JSON.parse(data.join("\n")));
      } catch (e) {
        /* A partial or malformed event is skipped rather than breaking the stream. */
      }
    });
    return remainder;
  }

  /* -------------------------------------------------------------------- ask */

  var turnCount = 0;

  function ask(question) {
    if (busy) { return; }
    busy = true;
    submit.disabled = true;
    input.value = "";
    turnCount += 1;
    var turnId = "turn" + turnCount;

    var turn = el("section");
    turn.id = turnId;
    turn.appendChild(el("h2", "section-heading", "Question"));
    turn.appendChild(el("p", null, question));
    var sectionsHeading = el("h2", "section-heading", "Governing sections");
    turn.appendChild(sectionsHeading);
    var cards = el("div", "cards");
    var searching = el("p", "spinner", "Searching…");
    cards.appendChild(searching);
    turn.appendChild(cards);
    conversation.appendChild(turn);
    turn.scrollIntoView({ behavior: "smooth", block: "start" });

    var answerBox = null;
    var answerBody = null;
    var provenance = null;
    var answerText = "";
    var sectionCount = 0;

    function ensureAnswerBox() {
      if (answerBox) { return; }
      turn.appendChild(el("h2", "section-heading", "Answer"));
      answerBox = el("div", "answer");
      provenance = el("div", "provenance", "");
      answerBox.appendChild(provenance);
      answerBody = el("div");
      answerBody.appendChild(el("span", "spinner", "Writing…"));
      answerBox.appendChild(answerBody);
      turn.appendChild(answerBox);
    }

    function showSections(payload) {
      searching.remove();
      var list = payload.sections || [];
      sectionCount = list.length;
      if (!list.length) {
        cards.appendChild(el(
          "p", "notice", "No matching section was found in the uploaded manuals."
        ));
      } else {
        list.forEach(function (section) {
          cards.appendChild(sectionCard(section, turnId));
        });
      }
      var notes = (payload.notes || []).slice();
      if (payload.used_embeddings === false) {
        notes.push("keyword search only, the embedding model was not available");
      }
      if (payload.rewritten && payload.question) {
        notes.push("searched as: " + payload.question);
      }
      if (notes.length) {
        var note = el("p", "pageline", notes.join(" · "));
        sectionsHeading.insertAdjacentElement("afterend", note);
      }
      ensureAnswerBox();
    }

    fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, history: history.slice(-3) })
    }).then(function (response) {
      if (response.status === 429) {
        searching.remove();
        cards.appendChild(el("p", "notice", "Too many questions in the last minute. Wait a moment and ask again."));
        throw new Error("rate limited");
      }
      if (!response.ok || !response.body) {
        throw new Error("HTTP " + response.status);
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";

      function onEvent(name, payload) {
        if (name === "sources") {
          showSections(payload);
        } else if (name === "token") {
          ensureAnswerBox();
          answerText += payload.t || "";
          renderAnswer(answerBody, answerText, turnId);
        } else if (name === "error") {
          ensureAnswerBox();
          answerBody.textContent = "";
          answerBody.appendChild(el("p", "notice", payload.detail || "Something went wrong."));
        } else if (name === "done") {
          ensureAnswerBox();
          if (answerText) { renderAnswer(answerBody, answerText, turnId); }
          var used = payload.source_count || 0;
          if (used > 0 && !payload.not_found) {
            provenance.textContent =
              "Answer based on " + used + (used === 1 ? " source" : " sources") +
              " · " + Math.round(payload.latency_ms / 100) / 10 + " s";
          } else {
            answerBox.classList.add("nosources");
            provenance.textContent = used > 0
              ? "Not answered from the manuals. Check the sections above."
              : "No source was used. Nothing in the uploaded manuals matched.";
          }
          if (payload.question_id) {
            answerBox.appendChild(ratingRow(payload.question_id));
          }
          history.push({ question: question, answer: answerText.slice(0, 4000) });
        }
      }

      function pump() {
        return reader.read().then(function (result) {
          if (result.done) {
            buffer = handleEvents(buffer + "\n\n", onEvent);
            return;
          }
          buffer += decoder.decode(result.value, { stream: true });
          buffer = handleEvents(buffer, onEvent);
          return pump();
        });
      }
      return pump();
    }).catch(function () {
      searching.remove();
      if (!answerBox) {
        cards.appendChild(el(
          "p", "notice",
          "The assistant could not be reached. Check that the service is running and try again."
        ));
      }
    }).finally(function () {
      busy = false;
      submit.disabled = false;
      input.focus();
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var question = input.value.trim();
    if (question) { ask(question); }
  });

  input.focus();
})();
