/* Manual Assistant, admin page.
 *
 * As on the chat page, every value from the server is inserted as text, never
 * as HTML. A document title or an ingest error message can contain anything.
 */
(function () {
  "use strict";

  var cfg = window.APP_CONFIG || {};
  var rows = document.getElementById("docrows");
  var message = document.getElementById("message");
  var form = document.getElementById("uploadform");
  var uploadButton = document.getElementById("uploadbutton");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  document.title = (cfg.appName || "Admin") + " - admin";
  document.getElementById("app-name").textContent = cfg.appName || "";

  var who = document.getElementById("who");
  if (cfg.user) {
    who.appendChild(el("span", null, cfg.user.username + " "));
    var back = el("a", null, "Chat");
    back.setAttribute("href", "/");
    who.appendChild(back);
    who.appendChild(el("span", null, " "));
    var logout = document.createElement("form");
    logout.setAttribute("method", "post");
    logout.setAttribute("action", "/logout");
    var logoutButton = el("button", null, "Sign out");
    logoutButton.setAttribute("type", "submit");
    logout.appendChild(logoutButton);
    who.appendChild(logout);
  }

  var productSelect = document.getElementById("product");
  (cfg.products || []).forEach(function (name) {
    var option = document.createElement("option");
    option.setAttribute("value", name);
    option.textContent = name;
    productSelect.appendChild(option);
  });

  function notify(text, isError) {
    message.textContent = "";
    if (!text) { return; }
    var box = el("div", "notice", text);
    if (!isError) { box.style.borderColor = "#a9cdb9"; box.style.background = "#eef7f2"; box.style.color = "#1d6b45"; }
    message.appendChild(box);
  }

  function pill(value, kind) {
    return el("span", "pill " + (kind || value), value);
  }

  function actionButton(label, handler) {
    var button = el("button", null, label);
    button.setAttribute("type", "button");
    button.style.marginRight = "6px";
    button.addEventListener("click", handler);
    return button;
  }

  function statusSelect(doc) {
    var select = document.createElement("select");
    ["current", "superseded"].forEach(function (value) {
      var option = document.createElement("option");
      option.setAttribute("value", value);
      option.textContent = value;
      if (doc.status === value) { option.setAttribute("selected", ""); }
      select.appendChild(option);
    });
    select.addEventListener("change", function () {
      var body = new FormData();
      body.append("doc_status", select.value);
      fetch("/api/admin/documents/" + doc.id, { method: "POST", body: body })
        .then(function (response) {
          if (!response.ok) { throw new Error("failed"); }
          notify("Status of “" + doc.title + "” set to " + select.value + ".", false);
          load();
        })
        .catch(function () { notify("Could not change the status.", true); });
    });
    return select;
  }

  function render(documents) {
    rows.textContent = "";
    if (!documents.length) {
      var empty = document.createElement("tr");
      var cell = el("td", null, "No documents yet. Upload a manual above.");
      cell.setAttribute("colspan", "9");
      empty.appendChild(cell);
      rows.appendChild(empty);
      return;
    }
    documents.forEach(function (doc) {
      var tr = document.createElement("tr");

      var titleCell = el("td", "wrap");
      titleCell.appendChild(el("div", null, doc.title));
      titleCell.appendChild(el("div", "pageline", doc.filename));
      tr.appendChild(titleCell);

      tr.appendChild(el("td", "wrap", doc.product || "—"));
      tr.appendChild(el("td", null, doc.version || "—"));

      var statusCell = document.createElement("td");
      statusCell.appendChild(statusSelect(doc));
      tr.appendChild(statusCell);

      var ingestCell = el("td", "wrap");
      ingestCell.appendChild(pill(doc.ingest_status, doc.ingest_status));
      if (doc.ingest_error) {
        ingestCell.appendChild(el("div", "err", doc.ingest_error));
      }
      tr.appendChild(ingestCell);

      tr.appendChild(el("td", null, doc.page_count || 0));
      tr.appendChild(el("td", null, doc.chunk_count || 0));

      var licenceCell = el("td", "wrap");
      licenceCell.appendChild(el("div", null, doc.licence_confirmed_by || "—"));
      licenceCell.appendChild(el("div", "pageline", doc.licence_confirmed_at || ""));
      tr.appendChild(licenceCell);

      var actions = document.createElement("td");
      actions.appendChild(actionButton("Re-index", function () {
        notify("Re-indexing “" + doc.title + "”…", false);
        fetch("/api/admin/reindex/" + doc.id, { method: "POST" })
          .then(function (response) { return response.json().then(function (data) {
            if (!response.ok) { throw new Error(data.detail || "failed"); }
            notify(
              data.error
                ? "“" + doc.title + "” failed: " + data.error
                : "“" + doc.title + "” re-indexed.",
              Boolean(data.error)
            );
            load();
          }); })
          .catch(function (error) { notify("Re-index failed: " + error.message, true); });
      }));
      actions.appendChild(actionButton("Delete", function () {
        if (!window.confirm("Delete “" + doc.title + "” and its index?")) { return; }
        fetch("/api/admin/documents/" + doc.id, { method: "DELETE" })
          .then(function (response) {
            if (!response.ok) { throw new Error("failed"); }
            notify("“" + doc.title + "” deleted.", false);
            load();
          })
          .catch(function () { notify("Could not delete the document.", true); });
      }));
      var open = el("a", null, "Open PDF");
      open.setAttribute("href", "/files/" + doc.id);
      open.setAttribute("target", "_blank");
      open.setAttribute("rel", "noopener");
      actions.appendChild(open);
      tr.appendChild(actions);

      rows.appendChild(tr);
    });
  }

  function load() {
    fetch("/api/admin/documents")
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (data) { render(data.documents || []); })
      .catch(function () { notify("Could not load the document list.", true); });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var fileInput = document.getElementById("file");
    if (!fileInput.files.length) { notify("Choose a PDF file.", true); return; }
    if (!document.getElementById("licence").checked) {
      notify("Tick the licence confirmation before uploading.", true);
      return;
    }
    var body = new FormData();
    body.append("file", fileInput.files[0]);
    body.append("title", document.getElementById("title").value);
    body.append("product", productSelect.value);
    body.append("version", document.getElementById("version").value);
    body.append("doc_status", document.getElementById("doc_status").value);
    body.append("licence_confirmed", "true");

    uploadButton.disabled = true;
    notify("Uploading and indexing…", false);
    fetch("/api/admin/upload", { method: "POST", body: body })
      .then(function (response) { return response.json().then(function (data) {
        if (!response.ok) { throw new Error(data.detail || "upload failed"); }
        var doc = data.document || {};
        if (doc.ingest_status === "failed") {
          notify("Uploaded, but indexing failed: " + (doc.ingest_error || data.error), true);
        } else {
          notify(
            "“" + doc.title + "” indexed: " + doc.page_count +
            " pages, " + doc.chunk_count + " chunks.",
            false
          );
          form.reset();
        }
        load();
      }); })
      .catch(function (error) { notify(error.message, true); })
      .finally(function () { uploadButton.disabled = false; });
  });

  load();
})();
