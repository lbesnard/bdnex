"use strict";
// bdnex web page: no build step, no framework. Everything shown from the server goes through textContent.
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
};

let token = "";
try { token = sessionStorage.getItem("bdnex-token") || ""; } catch (e) { /* storage may be blocked */ }
const app = { overview: null };
const view = $("#view");

// ------------------------------------------------------------------ talking to the server
async function api(path, { params = {}, method = "GET", body } = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) if (v !== "" && v !== false && v != null) url.searchParams.set(k, v);
  const headers = { "X-Bdnex-Page": "1" };  // the server then sends no Basic challenge: the page has its own login form
  if (token) headers.Authorization = "Bearer " + token;
  if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-Requested-With"] = "bdnex"; }
  const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && res.headers.get("X-Bdnex-Login") === "password") { location.reload(); return new Promise(() => {}); }  // the login form
  if (res.status === 401) { await askToken(); return api(path, { params, method, body }); }
  if (!res.ok) {
    let message = res.statusText;
    try { message = (await res.json()).detail || message; } catch (e) { /* not JSON */ }
    throw new Error(message);
  }
  return res.json();
}

function askToken() {
  return new Promise(resolve => {
    const input = el("input", { type: "password", placeholder: "access token", autofocus: true });
    render(el("h1", {}, "Access token"), el("p", { class: "lead" }, "This server asks for a token."),
      el("form", { class: "row", onsubmit: e => {
        e.preventDefault(); token = input.value;
        try { sessionStorage.setItem("bdnex-token", token); } catch (err) { /* ignore */ }
        resolve();
      } }, input, el("button", { class: "primary", type: "submit" }, "Unlock")));
  });
}

// Pictures cannot send the token header, so with a token they are fetched and shown from memory.
function picture(img, path) {
  if (!token) { img.src = path; return; }
  fetch(path, { headers: { Authorization: "Bearer " + token } })
    .then(r => r.ok ? r.blob() : Promise.reject(new Error(r.statusText)))
    .then(b => { img.src = URL.createObjectURL(b); })
    .catch(() => img.dispatchEvent(new Event("error")));
}

function cover(path, fallbackText) {
  const box = el("div", { class: "cover" });
  const img = el("img", { alt: "", loading: "lazy" });
  img.addEventListener("error", () => { img.remove(); box.append(el("span", { class: "ph" }, (fallbackText || "?").slice(0, 2).toUpperCase())); });
  box.append(img);
  if (path) picture(img, path); else img.dispatchEvent(new Event("error"));
  return box;
}

// ------------------------------------------------------------------ small helpers
const label = a => {
  const vol = a.volume != null ? "T" + String(a.volume).padStart(2, "0") : (a.alternate_number || "");
  const title = a.title && a.title !== a.series ? a.title : "";
  return [a.series, vol, title].filter(Boolean).join(" - ") || a.title || "(untitled)";
};
const size = n => n == null ? "" : n > 1073741824 ? (n / 1073741824).toFixed(1) + " GiB" : n > 1048576 ? (n / 1048576).toFixed(1) + " MiB" : Math.ceil(n / 1024) + " KiB";
const canChange = () => !!(app.overview && app.overview.allow_changes);
// `text in backticks` becomes code
const rich = text => String(text ?? "").split("`").map((part, i) => i % 2 ? el("code", {}, part) : part);
const header = (title, lead) => [el("h1", {}, title), lead ? el("p", { class: "lead" }, rich(lead)) : null];
const render = (...kids) => view.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));
const loading = () => render(el("p", { class: "hint" }, "Loading…"));
const failure = e => render(el("div", { class: "panel err" }, e.message));

function searchBox(placeholder, value, onsubmit) {
  const input = el("input", { type: "search", placeholder, value, "aria-label": "search" });
  return el("form", { class: "row", onsubmit: e => { e.preventDefault(); onsubmit(input.value.trim()); } },
    input, el("button", { class: "primary", type: "submit" }, "Search"));
}

// ------------------------------------------------------------------ the views
// A name in a credit, a series, a genre... is a link to the albums that have it: one term of the `bdnex ls` language,
// quoted as a whole so that `Jarry, Nicolas` stays one name.
const ROLE_FIELDS = new Set(["writer", "penciller", "inker", "colorist", "letterer", "coverartist"]);
const exact = (field, value) => '"' + `${field}:=${value}`.replace(/(["\\])/g, "\\$1") + '"';
const albumsWith = (field, value) => "#/albums?q=" + encodeURIComponent(exact(field, value));
function filterLink(field, value, text, hint) {
  return el("a", { href: albumsWith(field, value), title: hint || `All the albums with ${value}`, onclick: () => { const d = $("#dialog"); if (d.open) d.close(); } }, text ?? value);
}

const VIEWS = {
  overview: { name: "Overview", icon: "◧", async draw() {
    const o = app.overview = await api("/api/overview");
    const tile = (n, text, href, alert) => el("a", { class: "tile" + (alert && n ? " alert" : ""), href: href || "#/overview" }, el("b", {}, n), el("span", {}, text));
    const bars = (data, unit) => {
      const entries = Object.entries(data), max = Math.max(1, ...entries.map(e => e[1]));
      return el("div", { class: "bars" }, entries.map(([k, v]) => el("div", { class: "bar" },
        el("span", {}, k), el("span", {}, el("i", { style: `width:${Math.max(1, Math.round(100 * v / max))}%` })), el("span", {}, `${v}${unit || ""}`))));
    };
    render(...header("Your library", o.collection ? o.collection : "No collection folder is set yet."),
      el("div", { class: "tiles" },
        tile(o.albums, "albums", "#/albums"), tile(o.series, "series", "#/series"), tile(o.files, "files", "#/files"),
        tile(o.oneshots, "one-shots", "#/albums"),
        tile(o.review, "to review", "#/review", true), tile(o.corrupted, "damaged files", "#/health", true),
        tile(o.untagged, "files without an album", "#/files", true), tile(o.partial, "albums to fetch", "#/tasks", true)),
      el("div", { class: "cols" },
        el("div", { class: "panel" }, el("h3", {}, "Files by format"), bars(o.formats)),
        el("div", { class: "panel" }, el("h3", {}, "Albums by decade"), Object.keys(o.decades).length ? bars(o.decades) : el("p", { class: "hint" }, "No year known yet."))),
      o.sources.length ? [el("h2", {}, "Import sources"), el("div", { class: "panel" }, o.sources.map(s =>
        el("div", { class: "row" }, el("b", {}, s.name), el("span", { class: "tag" }, s.mode), el("span", { class: "hint" }, s.path))))] : null,
      el("p", { class: "hint" }, `${o.with_cover} of ${o.albums} albums have a saved cover.`));
  } },

  albums: { name: "Albums", icon: "▦", async draw(q = "", offset = 0, previous = []) {
    const data = await api("/api/albums", { params: { q, limit: 60, offset } });
    const items = [...previous, ...data.items];
    const cards = items.map(a => el("div", { class: "card", tabindex: 0, onclick: () => albumDialog(a.id), onkeydown: e => { if (e.key === "Enter") albumDialog(a.id); } },
      el("div", { class: "cover" }, a.partial ? el("span", { class: "badge" }, "not fetched") : null, cover(`/api/albums/${a.id}/cover`, a.title)),
      el("div", { class: "meta" }, el("div", { class: "t" }, a.series ? `${a.series}${a.volume != null ? " T" + a.volume : ""}` : a.title),
        el("div", { class: "s" }, [a.series ? a.title : null, a.year].filter(Boolean).join(" · ")))));
    const next = offset + data.items.length;
    render(...header("Albums", `${data.count} match${data.count === 1 ? "" : "es"}`),
      searchBox("series:nains year:2010..2015 ^genre:humour", q, v => VIEWS.albums.draw(v, 0)),
      q.trim() && data.count && canChange() ? el("div", { class: "row", style: "margin-top:8px" }, el("button", { onclick: () => bulkEdit(q, data.count) }, `Edit these ${data.count} album(s)…`)) : null,
      el("p", { class: "hint" }, rich("Same terms as `bdnex ls`: field:value, ranges, ::regex, ^ to exclude.")),
      cards.length ? el("div", { class: "grid" }, cards) : el("p", { class: "hint" }, "No album."),
      next < data.count ? el("div", { class: "more" }, el("button", { onclick: () => VIEWS.albums.draw(q, next, items) }, `Show more (${data.count - next})`)) : null);
  } },

  people: { name: "People", icon: "☺", async draw(q = "", offset = 0) {
    const data = await api("/api/people", { params: { q, limit: 100, offset } });
    const rows = data.items.map(p => el("tr", {}, el("td", {}, el("a", { href: albumsWith("author", p.name) }, p.name)), el("td", {}, String(p.albums)),
      el("td", {}, Object.entries(p.roles).flatMap(([role, n], i) => [i ? " · " : "", filterLink(role.toLowerCase() in { writer: 1, penciller: 1, inker: 1, colorist: 1, letterer: 1, coverartist: 1 } ? role.toLowerCase() : "author", p.name, `${role} ${n}`, `${p.name} as ${role}`)]))));
    const to = offset + data.items.length;
    render(...header("People", `${data.count} credited name(s). Each name is one entry: the same person under two spellings is two entries.`),
      searchBox("part of a name", q, v => VIEWS.people.draw(v, 0)),
      el("div", { class: "panel scroll", style: "margin-top:12px" }, data.items.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Name", "Albums", "Roles"].map(h => el("th", {}, h)))), el("tbody", {}, rows)) : "Nobody found."),
      el("div", { class: "more" }, el("button", { disabled: offset === 0, onclick: () => VIEWS.people.draw(q, Math.max(0, offset - 100)) }, "‹ Previous"), " ",
        el("button", { disabled: to >= data.count, onclick: () => VIEWS.people.draw(q, to) }, "Next ›")));
  } },

  series: { name: "Series", icon: "☰", async draw(name = "", onlyMissing = false) {
    const data = await api("/api/series", { params: { name, missing: onlyMissing } });
    const only = el("input", { type: "checkbox", id: "only" }); only.checked = onlyMissing;
    const form = el("form", { class: "row", onsubmit: e => { e.preventDefault(); VIEWS.series.draw($("input[type=search]", form).value.trim(), only.checked); } },
      el("input", { type: "search", placeholder: "words of the series name", value: name, "aria-label": "series name" }),
      el("label", { class: "hint" }, only, " only incomplete"), el("button", { class: "primary", type: "submit" }, "Search"));
    const rows = data.items.map(s => {
      const pct = s.known ? Math.round(100 * s.owned / s.known) : 100;
      const body = el("div", {});
      let opened = false;
      const head = el("div", { class: "s-head", onclick: async () => {
        if (opened) { body.replaceChildren(); opened = false; return; }
        opened = true; body.replaceChildren(el("p", { class: "hint" }, "Loading…"));
        try { body.replaceChildren(await seriesVolumes(s.id)); } catch (e) { body.replaceChildren(el("p", { class: "err" }, e.message)); }
      } }, el("strong", {}, s.name),
        el("span", { class: "tag " + (s.known == null ? "" : s.missing.length ? "warn" : "ok") },
          s.known == null ? "list unknown" : s.missing.length ? `missing ${s.missing.length}` : "complete"),
        el("div", { class: "meter" + (s.missing && s.missing.length ? " partial" : "") }, el("i", { style: `width:${pct}%` })),
        el("span", { class: "hint", style: "margin:0;min-width:64px;text-align:right" }, s.known == null ? `${s.owned}` : `${s.owned} / ${s.known}`));
      return el("div", { class: "panel" }, head, body);
    });
    render(...header("Series", "Click a series to see which volumes you have."), form, el("div", { class: "series" }, rows.length ? rows : el("p", { class: "hint" }, "No series.")));
    if (data.items.length === 1) $(".s-head", view).click();  // a search that finds one series opens it
  } },

  review: { name: "Review", icon: "✓", async draw() {
    const data = await api("/api/review");
    const out = [...header("Review matches", "Files whose cover does not look like their album's. Is the match right?")];
    if (!canChange()) out.push(el("p", { class: "hint warn" }, rich("This server is read-only: start it with `api.allow_changes: true` (or `--allow-changes`) to answer here. Meanwhile `bdnex review` does the same in the terminal.")));
    if (!data.items.length) out.push(el("div", { class: "panel" }, "Nothing waits for a review."));
    const list = el("div", { class: "review" });
    for (const item of data.items) {
      const card = el("div", { class: "panel r-card" });
      const answer = async action => {
        try { await api(`/api/review/${item.file_id}`, { method: "POST", body: { action } }); card.remove(); refreshBadge(); }
        catch (e) { card.append(el("p", { class: "err" }, e.message)); }
      };
      card.append(
        el("div", { class: "pair" },
          el("figure", {}, cover(`/api/files/${item.file_id}/cover`, "file"), el("figcaption", {}, "the file")),
          el("figure", {}, cover(item.album.has_cover ? `/api/albums/${item.album.id}/cover` : null, "?"), el("figcaption", {}, item.album.has_cover ? "the album on the site" : "cover not downloaded yet"))),
        el("div", { class: "r-text" }, el("h3", {}, label(item.album)), el("p", { class: "warn" }, item.reason),
          el("p", { class: "path" }, item.path),
          el("div", { class: "row" },
            el("button", { class: "primary", disabled: !canChange(), onclick: () => answer("keep") }, "Keep the match"),
            el("button", { disabled: !canChange(), onclick: () => confirm("Unlink this file from the album? It stays on disk.") && answer("unlink") }, "Unlink"))));
      list.append(card);
    }
    render(...out, list);
  } },

  health: { name: "Health", icon: "✚", async draw() {
    const data = await api("/api/check");
    const dups = await api("/api/duplicates");
    const out = [...header("Library health", "Is the library consistent with itself and with the disk?")];
    if (!data.items.length) out.push(el("div", { class: "panel ok" }, "Everything is consistent."));
    const list = el("div", { class: "findings" });
    for (const f of data.items) list.append(el("div", { class: "panel finding" },
      el("div", { class: "row" }, el("strong", {}, f.problem), f.fixable ? el("span", { class: "tag ok" }, "can be fixed") : null),
      f.advice ? el("div", { class: "hint" }, rich(f.advice)) : null, f.items.length ? el("ul", {}, f.items.map(i => el("li", {}, i))) : null));
    out.push(list);
    if (data.items.some(f => f.fixable)) out.push(el("p", {}, el("button", { class: "primary", disabled: !canChange(),
      onclick: () => confirm("Settle what is safe: remove empty series, forget missing cover files, dead trash records and temporary files?") && startJob("check-fix", { preview: false }) }, "Fix what is safe")));
    out.push(el("h2", {}, "The same book twice"));
    if (!dups.items.length) out.push(el("div", { class: "panel" }, "No duplicates found."));
    for (const g of dups.items) out.push(el("div", { class: "panel", style: "margin-bottom:10px" }, el("strong", {}, g.why),
      g.files.map(f => el("div", { class: "hint", style: "margin:4px 0" }, `${f.format} · ${f.pages ?? "?"} pages · ${size(f.size)}${f.spare ? " · spare" : ""} — ${f.path}`))));
    render(...out);
  } },

  tasks: { name: "Tasks", icon: "▶", async draw(jobId) {
    const available = await api("/api/jobs/available");
    const history = await api("/api/jobs");
    app.overview = app.overview || await api("/api/overview");
    const logBox = el("div", {});
    const query = el("input", { type: "text", placeholder: "only these files (same terms as bdnex ls --files), e.g. series:nains" });
    const folder = el("select", {}, el("option", { value: "" }, "your whole collection"),
      el("option", { value: app.overview.collection || "" }, "collection folder"), ...(app.overview.sources || []).map(s => el("option", { value: s.path }, "source: " + s.name)));
    const askHere = el("input", { type: "checkbox", checked: true });
    const choose = (...options) => el("select", {}, options.map(([value, text]) => el("option", { value }, text)));
    const cards = available.items.map(j => {
      const has = name => j.fields.includes(name);
      const steps = choose(["", "match and read the pages"], ["match", "match only"], ["fetch", "read the pages only"]);
      const files = choose(["", "files as configured"], ["database_only", "touch no file (database only)"], ["no_move", "leave files where they are"]);
      const auto = choose(["", "ask about every match"], ["strong", "ask only when unsure"], ["all", "never ask"]);
      const skipped = el("label", { class: "tick" }, el("input", { type: "checkbox", id: "only-skipped" }), " only the files skipped earlier");
      const formats = choose(["pdf", "PDF files"], ["cbr", "CBR files"], ["pdf,cbr", "PDF and CBR files"]);
      const days = el("input", { type: "number", min: 0, placeholder: "older than (days), or all" });
      const oldDir = el("input", { type: "text", placeholder: "the folder the files used to be in" }), newDir = el("input", { type: "text", placeholder: "the folder they are in now" });
      const extras = el("div", { class: "row" }, has("import") ? [steps, files, auto, skipped] : null, has("formats") ? formats : null, has("older_than") ? days : null, has("relocate") ? [oldDir, newDir] : null);
      const opts = () => ({ query: has("query") ? query.value.split(/\s+/).filter(Boolean) : [], paths: has("paths") && folder.value ? [folder.value] : [],
                            copy_files: false, interactive: askHere.checked,
                            ...(has("import") ? { steps: steps.value ? [steps.value] : [], files: files.value, auto: auto.value, skipped: skipped.querySelector("input").checked } : {}),
                            ...(has("formats") ? { formats: formats.value.split(",") } : {}),
                            ...(has("older_than") && days.value !== "" ? { older_than: Number(days.value) } : {}),
                            ...(has("relocate") ? { old: oldDir.value, new: newDir.value } : {}) });
      const scary = j.asks || j.command === "trash-empty";
      const real = el("button", { class: "danger", disabled: !available.allow_changes,
        onclick: () => confirm(scary ? `“${j.label}” cannot be taken back. Go on?` : `Run “${j.label}” for real? It changes your files or your library.`) && startJob(j.command, { ...opts(), preview: false }, logBox) },
        j.writes ? "Run for real" : "Run");
      return el("div", { class: "panel job" }, el("h3", {}, j.label), extras.children.length ? extras : null,
        el("div", { class: "row" },
          j.preview ? el("button", { onclick: () => startJob(j.command, { ...opts(), preview: true }, logBox) }, j.writes ? "Preview" : "Run") : null,
          j.writes ? real : null));
    });
    render(...header("Tasks", "The same operations as the command line. A preview changes nothing; run it first."),
      available.allow_changes ? null : el("p", { class: "hint warn" }, rich("Read-only server: previews and checks only. Set `api.allow_changes: true` to run for real.")),
      el("div", { class: "row", style: "margin-bottom:12px" }, query, folder, el("label", { class: "tick" }, askHere, " ask me my questions here")),
      logBox, el("div", { class: "jobs" }, cards),
      el("h2", {}, "Earlier runs"), el("div", { class: "panel" }, history.items.length ? history.items.map(j =>
        el("div", { class: "row" }, el("b", {}, `#${j.id}`), j.label, el("span", { class: "tag " + (j.status === "done" ? "ok" : j.status === "running" ? "warn" : "bad") }, j.status),
          el("button", { onclick: () => followJob(j.id, logBox) }, "Show"))) : el("span", { class: "hint" }, "Nothing has run yet.")));
    const running = history.items.find(j => j.status === "running");
    if (jobId) followJob(Number(jobId), logBox); else if (running) followJob(running.id, logBox);
  } },

  files: { name: "Files", icon: "▤", async draw(q = "", offset = 0) {
    const data = await api("/api/files", { params: { q, limit: 100, offset } });
    const again = () => VIEWS.files.draw(q, offset);
    const post = async (f, what, body) => { try { const r = await api(`/api/files/${f.file_id}/${what}`, { method: "POST", body: body || {} }); if (r.note) alert(r.note); again(); } catch (e) { alert(e.message); } };
    const run = (command, f, extra) => startJob(command, { query: [`fileid:${f.file_id}`], preview: false, interactive: true, ...extra });
    const actions = f => !canChange() ? "" : el("details", { class: "acts" }, el("summary", {}, "Actions"), el("div", { class: "menu" },
      f.id ? el("button", { onclick: () => albumDialog(f.id) }, "Album") : null,
      f.id ? el("button", { onclick: () => run("write", f) }, "Write tags") : null,
      f.id ? el("button", { onclick: () => run("move", f) }, "Move into place") : null,
      f.corrupted ? el("button", { onclick: () => run("repair", f) }, "Repair") : null,
      el("button", { title: "Choose its album again", onclick: () => startJob("import", { paths: [f.path], steps: ["match"], force: true, files: "no_move", preview: false, interactive: true }) }, "Rematch"),
      f.id ? el("button", { onclick: () => post(f, "spare", { spare: !f.spare }) }, f.spare ? "Not a spare version" : "Mark as spare version") : null,
      f.id ? el("button", { onclick: () => confirm(`Forget which album ${f.path} belongs to?`) && post(f, "unlink") }, "Unlink from its album") : null,
      el("button", { class: "danger", onclick: () => confirm(`Move ${f.path} to the trash? (Restore it from the Trash page.)`) && post(f, "trash", { reason: "moved to the trash from the Files page" }) }, "Move to the trash")));
    const rows = data.items.map(f => el("tr", {}, el("td", {}, f.path), el("td", {}, f.format), el("td", {}, size(f.size)),
      el("td", {}, f.corrupted ? el("span", { class: "tag bad" }, "damaged") : "", f.spare ? el("span", { class: "tag" }, "spare") : "", f.review ? el("span", { class: "tag warn" }, "to review") : ""),
      el("td", {}, actions(f))));
    const to = offset + data.items.length;
    render(...header("Files", `${data.count} file(s)`), searchBox("format:cbz corrupted:true tagged:false", q, v => VIEWS.files.draw(v, 0)),
      el("div", { class: "panel scroll", style: "margin-top:12px" }, el("table", {}, el("thead", {}, el("tr", {}, ["Path", "Format", "Size", "", ""].map(h => el("th", {}, h)))), el("tbody", {}, rows))),
      el("div", { class: "more" }, el("button", { disabled: offset === 0, onclick: () => VIEWS.files.draw(q, Math.max(0, offset - 100)) }, "‹ Previous"), " ",
        el("button", { disabled: to >= data.count, onclick: () => VIEWS.files.draw(q, to) }, "Next ›")));
  } },

  trash: { name: "Trash", icon: "✕", async draw() {
    const data = await api("/api/trash");
    const restore = (t, place) => confirm(`Put ${t.path} back${place ? " and move it where its album belongs" : ""}?`) &&
      startJob("trash-restore", { id: t.id, place, preview: false, interactive: true });
    render(...header("Trash", "Files that were replaced wait here. Empty them from the Tasks page."),
      el("div", { class: "panel scroll" }, data.items.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Was at", "Why", "Size", "Trashed", ""].map(h => el("th", {}, h)))),
        el("tbody", {}, data.items.map(t => el("tr", {}, el("td", {}, t.path), el("td", {}, t.reason), el("td", {}, size(t.size)), el("td", {}, t.trashed),
          el("td", {}, canChange() ? el("div", { class: "row" }, el("button", { onclick: () => restore(t, false) }, "Restore"), el("button", { onclick: () => restore(t, true) }, "Restore and place")) : ""))))) : "The trash is empty."));
  } },

  settings: { name: "Settings", icon: "⚙", async draw() {
    const data = await api("/api/settings");
    const edit = canChange();
    // one control per option; `value()` is what it holds now, in the type the server wants
    const control = o => {
      let input, value;
      const editable = o.editable && edit;
      if (o.kind === "choice") {
        input = el("select", { disabled: !editable }, o.choices.map(c => el("option", { value: JSON.stringify(c) }, String(c))));
        input.value = JSON.stringify(o.value); value = () => JSON.parse(input.value);
      } else if (o.kind === "bool") {
        input = el("input", { type: "checkbox", disabled: !editable }); input.checked = !!o.value; value = () => input.checked;
      } else if (o.kind === "int" || o.kind === "float") {
        input = el("input", { type: "number", step: o.kind === "int" ? 1 : "any", min: o.min, max: o.max, disabled: !editable, value: o.value ?? "" });
        value = () => input.value === "" ? null : Number(input.value);
      } else {
        input = el("input", { type: "text", disabled: !editable, value: o.value ?? "" }); value = () => input.value === "" && o.nullable ? null : input.value;
      }
      const shown = JSON.stringify(o.default);
      const wrap = el("div", { class: "opt" }, el("label", { class: "optname" }, el("code", {}, o.key), o.yours ? el("span", { class: "tag" }, "set by you") : null, o.restart ? el("span", { class: "tag warn" }, "restart") : null),
        o.kind === "bool" ? el("label", { class: "tick" }, input, " on") : input,
        el("small", { class: "hint" }, rich(o.help), shown !== undefined && shown !== "null" ? ` (default: ${shown.replace(/^"|"$/g, "")})` : ""));
      return { wrap, key: o.key, changed: () => editable && JSON.stringify(value()) !== JSON.stringify(o.value), value };
    };
    const sections = data.sections.map((section, n) => {
      const controls = section.options.map(control), out = el("pre", { class: "log", hidden: true });
      const send = async preview => {
        const changes = Object.fromEntries(controls.filter(c => c.changed()).map(c => [c.key, c.value()]));
        out.hidden = false;
        if (!Object.keys(changes).length) { out.textContent = "Nothing changed."; return; }
        try {
          const r = await api("/api/settings", { method: "POST", body: { changes, preview } });
          out.textContent = r.changes.join("\n") + (preview ? "\n\nNot saved yet." : "\n\nSaved." + (r.restart.length ? ` Restart the server for ${r.restart.join(", ")}.` : " It applies now.") + " The previous file is kept as a .bak.");
        } catch (e) { out.textContent = e.message; }
      };
      return el("details", { class: "panel optsec", open: n === 0 }, el("summary", {}, section.name),
        controls.map(c => c.wrap), out,
        edit && section.options.some(o => o.editable) ? el("div", { class: "row" }, el("button", { onclick: () => send(true) }, "Check the changes"), el("button", { class: "primary", onclick: () => send(false) }, "Save")) : null);
    });
    render(...header("Settings", edit ? `Your configuration file (${data.path}). A change is checked as a whole before it is written; secrets and folders are edited in the file.` : "Shown from your configuration file. Changes need `api.allow_changes`."), ...sections);
  } },
};

// ------------------------------------------------------------------ dialogs and jobs
// ------------------------------------------------------------------ the metadata editor
const ROLES = ["Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist", "Editor", "Translator"];
const splitList = text => text.split(";").map(s => s.trim()).filter(Boolean);

function editForm(a, dialog, note) {
  const whole = (text) => text.trim() === "" ? null : /^-?\d+$/.test(text.trim()) ? Number(text.trim()) : text.trim();  // a bad number is sent as text: the server says why
  const field = (key, name, opts = {}) => {
    const input = el(opts.area ? "textarea" : "input", { type: opts.area ? null : "text", rows: opts.area ? 5 : null, inputmode: opts.number ? "numeric" : null,
      value: opts.area ? null : (a[key] ?? ""), "aria-label": name });
    if (opts.area) input.value = a[key] ?? "";
    return { key, input, wrap: el("label", { class: "f " + (opts.wide ? "wide" : "") }, el("span", {}, name), input, opts.hint ? el("small", { class: "hint" }, opts.hint) : null), number: opts.number };
  };
  const fields = [
    ...(a.kind === "series" ? [field("series", "Series", { hint: "Renames the series for all its albums." })] : []),
    field("title", "Title", { wide: true }), field("volume", "Volume", { number: true }), field("alternate_number", "Alternate number"),
    field("publisher", "Publisher"), field("year", "Year", { number: true }), field("month", "Month", { number: true }), field("day", "Day", { number: true }),
    field("pages", "Pages", { number: true }), field("language", "Language"), field("format", "Format"), field("isbn", "ISBN"),
    field("summary", "Summary", { area: true, wide: true })];
  const genres = el("input", { type: "text", value: (a.genres || []).join("; "), "aria-label": "Genres" });
  const roleRows = el("div", { class: "roles" });
  const addRole = (role = "", names = []) => {
    const r = el("input", { type: "text", value: role, list: "roles", placeholder: "role", "aria-label": "role" });
    const n = el("input", { type: "text", value: names.join("; "), placeholder: "names, separated by ; (Name, First is one name)", "aria-label": "names" });
    roleRows.append(el("div", { class: "row role" }, r, n));
  };
  for (const [role, names] of Object.entries(a.credits || {})) addRole(role, names);
  const roleList = el("datalist", { id: "roles" }, ROLES.map(r => el("option", { value: r })));
  const out = el("pre", { class: "log", hidden: true });
  const collect = () => {
    const doc = {};
    for (const f of fields) doc[f.key] = f.number ? whole(f.input.value) : (f.input.value.trim() === "" ? null : f.input.value.trim());
    doc.genres = splitList(genres.value);
    const credits = {};
    for (const row of roleRows.children) {
      const [r, n] = row.querySelectorAll("input"); const role = r.value.trim();
      if (role) credits[role] = splitList(n.value);
    }
    for (const role of Object.keys(a.credits || {})) if (!(role in credits)) credits[role] = [];  // an emptied row removes the role
    doc.credits = credits;
    return doc;
  };
  const send = async preview => {
    out.hidden = false; out.textContent = preview ? "Checking…" : "Saving…";
    try {
      const r = await api(`/api/albums/${a.id}/edit`, { method: "POST", body: { preview, fields: collect() } });
      if (!r.changed) { out.textContent = "Nothing differs from what is stored."; return; }
      if (preview) { out.textContent = r.changes.join("\n") + "\n\nNot saved yet."; return; }
      albumDialog(a.id, "Saved. The files still have their old tags until you write them.");
    } catch (e) { out.textContent = e.message; }
  };
  return el("form", { class: "edit", onsubmit: e => { e.preventDefault(); send(false); } },
    note ? el("p", { class: "hint ok" }, note) : null,
    el("div", { class: "fields" }, fields.map(f => f.wrap), el("label", { class: "f wide" }, el("span", {}, "Genres (separated by ;)"), genres)),
    el("h3", {}, "Credits"), roleRows, roleList, el("button", { type: "button", onclick: () => addRole() }, "Add a role"),
    out, el("div", { class: "row", style: "margin-top:12px" }, el("button", { type: "button", onclick: () => send(true) }, "Preview the changes"),
      el("button", { class: "primary", type: "submit" }, "Save"), el("button", { type: "button", onclick: () => albumDialog(a.id) }, "Cancel")));
}

async function bulkEdit(q, count) {
  const dialog = $("#dialog");
  const names = ["publisher", "language", "format", "year", "month", "day", "pages", "isbn", "genres", "credits.Writer", "credits.Penciller", "credits.Colorist", "series", "title", "summary"];
  const rows = el("div", {}), out = el("pre", { class: "log", hidden: true });
  const addRow = () => rows.append(el("div", { class: "row role" }, el("select", {}, names.map(n => el("option", { value: n }, n))), el("input", { type: "text", placeholder: "value (lists: a; b). Empty clears it", "aria-label": "value" })));
  addRow();
  const items = () => [...rows.children].map(r => `${r.querySelector("select").value}=${r.querySelector("input").value}`);
  const send = async preview => {
    out.hidden = false; out.textContent = preview ? "Checking…" : "Saving…";
    try {
      const r = await api("/api/edit", { method: "POST", body: { q, set: items(), preview } });
      out.textContent = `${r.matched} album(s) found, ${r.changed} would change${r.skipped ? `, ${r.skipped} skipped (one-shots have no series)` : ""}.\n\n` + r.changes.join("\n") +
        (preview ? "\n\nNot saved yet." : r.stored ? "\n\nSaved. Write the tags into the files from the Tasks page." : "\n\nNothing to save.");
    } catch (e) { out.textContent = e.message; }
  };
  dialog.replaceChildren(el("button", { class: "d-close", "aria-label": "close", onclick: () => dialog.close() }, "×"),
    el("div", { style: "padding:18px" }, el("h2", { style: "margin-top:0" }, "Change every album of this search"), el("p", { class: "hint" }, rich(`Search: \`${q}\` (${count} album(s)). Each line sets one field on all of them.`)),
      rows, el("div", { class: "row", style: "margin-top:8px" }, el("button", { onclick: addRow }, "Add a field")), out,
      el("div", { class: "row", style: "margin-top:12px" }, el("button", { onclick: () => send(true) }, "Preview"), el("button", { class: "primary", disabled: !canChange(), onclick: () => confirm(`Change ${count} album(s)?`) && send(false) }, "Save"))));
  dialog.showModal();
}

async function albumDialog(id, note) {
  const a = await api("/api/albums/" + id);
  const joined = nodes => nodes.flatMap((n, i) => i ? [", ", n] : [n]);
  const credits = Object.entries(a.credits || {}).map(([role, names]) => [role, joined(names.map(name => {
    const field = role.toLowerCase();
    return el("span", {}, filterLink("author", name, name, `All the albums ${name} worked on, in any role`),
      ROLE_FIELDS.has(field) ? el("small", { class: "hint" }, " (", filterLink(field, name, "as " + role.toLowerCase(), `Only the albums where ${name} is ${role.toLowerCase()}`), ")") : null);
  }))]);
  const rows = [["Series", a.series ? filterLink("series", a.series) : null], ["Year", a.year], ["Language", a.language],
    ["Publisher", a.publisher ? filterLink("publisher", a.publisher) : null], ["ISBN", a.isbn],
    ["Genres", (a.genres || []).length ? joined(a.genres.map(g => filterLink("genre", g))) : null], ...credits];
  const dialog = $("#dialog");
  const writeTags = () => { dialog.close(); startJob("write", { query: ["id:" + id], preview: false, interactive: true }); };
  dialog.replaceChildren(el("button", { class: "d-close", "aria-label": "close", onclick: () => dialog.close() }, "×"),
    el("div", { class: "d-body" }, cover(`/api/albums/${id}/cover`, a.title),
      el("div", {}, el("h2", { style: "margin-top:0" }, label(a)), a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener" }, "Open the album page") : null,
        canChange() ? el("div", { class: "row", style: "margin:8px 0" }, el("button", { onclick: () => dialog.replaceChildren(el("button", { class: "d-close", "aria-label": "close", onclick: () => dialog.close() }, "×"), el("div", { style: "padding:18px" }, el("h2", { style: "margin-top:0" }, label(a)), editForm(a, dialog))) }, "Edit"),
          a.files.length ? el("button", { onclick: writeTags, title: "Write this album's values into its files" }, "Write the tags into its files") : null) : null,
        note ? el("p", { class: "hint ok" }, note) : null,
        el("dl", {}, rows.filter(r => r[1]).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, Array.isArray(v) || v instanceof Node ? v : String(v))])),
        a.summary ? el("p", {}, a.summary) : null, el("h3", {}, `Files (${a.files.length})`),
        a.files.map(f => el("div", { class: "hint", style: "margin:2px 0" }, `${f.format} · ${size(f.size)} — ${f.path}`)))));
  if (!dialog.open) dialog.showModal();
}

async function seriesVolumes(id) {
  const s = await api("/api/series/" + id);
  const tiles = s.volumes.map(v => {
    const text = v.volume != null ? String(v.volume) : "·";
    const tip = `${v.title || ""}${v.owned ? "" : " (missing)"}`;
    if (v.owned && v.album_id) return el("button", { class: "vol", title: tip, onclick: () => albumDialog(v.album_id) }, text);
    return el(v.url ? "a" : "span", { class: "vol missing", title: tip, href: v.url || null, target: v.url ? "_blank" : null, rel: v.url ? "noopener" : null }, text);
  });
  return el("div", {}, el("div", { class: "vols" }, tiles), el("p", { class: "legend" }, s.list_known ? "Filled: you have it. Dashed: missing (opens its page)." : "The list of volumes is unknown: run `bdnex import --fetch`."));
}

// A job's question: the same answers, keys and colours as in the terminal (the server sends them).
function labelNodes(text) {  // "[S]kip it" -> S in a key box, "kip it" after
  const out = []; let last = 0;
  for (const m of text.matchAll(/\[(\w)\]/g)) { out.push(text.slice(last, m.index), el("kbd", {}, m[1].toUpperCase())); last = m.index + m[0].length; }
  out.push(text.slice(last)); return out.filter(x => x !== "");
}

function questionCard(jobId, q) {
  const card = el("div", { class: "panel question", role: "group", "aria-label": "A question" }, el("h3", {}, q.message));
  if (q.image) {  // what the terminal draws beside the question: the file's first page, or the two covers of a review
    const img = el("img", { class: "qimg", alt: "the file's first page" }); picture(img, `/api/jobs/${jobId}/image?q=${q.id}`); card.append(img);
  }
  let sent = false;
  const send = async body => {
    if (sent) return; sent = true; card.classList.add("sending");
    try { await api(`/api/jobs/${jobId}/answer`, { method: "POST", body: { question: q.id, ...body } }); }
    catch (e) { sent = false; card.classList.remove("sending"); alert(e.message); }
  };
  if (q.kind === "select") {
    const keys = {};
    for (const c of q.choices) {
      if (c.key) keys[c.key] = c.index;
      const answer = el("button", { class: "ans " + (c.tone || ""), onclick: () => send({ choice: c.index }) }, labelNodes(c.label));
      card.append(c.url && /^https?:\/\//.test(c.url) ? el("div", { class: "withlink" }, answer,
        el("a", { class: "btn", href: c.url, target: "_blank", rel: "noopener noreferrer", title: "Open the album's page on the site" }, "page ↗")) : answer);
    }
    card.onkey = e => { const i = keys[e.key.toLowerCase()]; if (i != null && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); send({ choice: i }); } };
  } else if (q.kind === "search") {
    const box = el("input", { type: "text", placeholder: "type to filter", autofocus: true }), list = el("div", { class: "pick" });
    const draw = () => {
      const words = box.value.toLowerCase().split(/\s+/).filter(Boolean);
      const found = q.choices.filter(c => words.every(w => c.label.toLowerCase().includes(w)));
      list.replaceChildren(...found.slice(0, 60).map(c => el("button", { class: "ans", onclick: () => send({ choice: c.index }) }, c.label)),
        found.length > 60 ? el("p", { class: "hint" }, `${found.length - 60} more: type more words`) : "");
    };
    box.addEventListener("input", draw); draw();
    card.append(box, list, el("button", { onclick: () => send({ choice: null }) }, "Go back"));
  } else if (q.kind === "text") {
    const box = el("input", { type: "text", value: q.default || "", autofocus: true });
    card.append(el("form", { class: "row", onsubmit: e => { e.preventDefault(); send({ text: box.value }); } }, box, el("button", { class: "primary", type: "submit" }, "OK")));
  } else if (q.kind === "checkbox") {
    const boxes = q.choices.map(c => el("input", { type: "checkbox", checked: c.ticked }));
    card.append(...q.choices.map((c, i) => el("label", { class: "tick" }, boxes[i], " ", c.label)),
      el("button", { class: "primary", onclick: () => send({ choices: q.choices.filter((c, i) => boxes[i].checked).map(c => c.index) }) }, "OK"));
  }
  return card;
}

let polling = null;
async function startJob(command, options, box) {
  try {
    const job = await api("/api/jobs", { method: "POST", body: { command, ...options } });
    if (!box) { location.hash = "#/tasks?job=" + job.id; return; }  // the Tasks page follows the job it is given
    followJob(job.id, box);
  } catch (e) { alert(e.message); }
}

async function followJob(id, box) {
  box = box || view;
  if (polling) clearTimeout(polling);
  const status = el("span", { class: "tag warn" }, "running"), log = el("pre", { class: "log" }), cancel = el("button", { disabled: !canChange() }, "Cancel");
  const asking = el("div", {});
  box.replaceChildren(el("div", { class: "row", style: "margin-top:14px" }, el("b", {}, `Job #${id}`), status, cancel), asking, log);
  let shown = 0, onkey = null;
  const stopKeys = () => { if (onkey) document.removeEventListener("keydown", onkey); onkey = null; };
  cancel.addEventListener("click", async () => { try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); } catch (e) { alert(e.message); } });
  let after = 0;
  const tick = async () => {
    let job;
    try { job = await api(`/api/jobs/${id}`, { params: { after } }); } catch (e) { log.append(e.message); return; }
    if (job.lines.length) { log.append(job.lines.join("\n") + "\n"); log.scrollTop = log.scrollHeight; }
    after = job.next;
    const waitingFor = job.status === "running" && job.question ? job.question : null;
    if ((waitingFor ? waitingFor.id : 0) !== shown) {  // a new question (or none): draw it once, so typing is not lost
      stopKeys(); asking.replaceChildren(); shown = waitingFor ? waitingFor.id : 0;
      if (waitingFor) {
        const card = questionCard(id, waitingFor); asking.append(card);
        if (card.onkey) { onkey = e => { if (!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) card.onkey(e); }; document.addEventListener("keydown", onkey); }
      }
    }
    status.textContent = waitingFor ? "waiting for you" : job.status;
    status.className = "tag " + (job.status === "done" ? "ok" : job.status === "running" ? "warn" : "bad");
    if (job.status === "running") polling = setTimeout(tick, waitingFor ? 1000 : 700); else { stopKeys(); asking.replaceChildren(); cancel.disabled = true; refreshBadge(); }
  };
  tick();
}

// ------------------------------------------------------------------ navigation
async function refreshBadge() {
  let overview;
  try { overview = await api("/api/overview"); } catch (e) { return; }
  const key = o => o && JSON.stringify([o.review, o.busy, o.allow_changes, o.watch && o.watch.enabled]);
  const before = key(app.overview);
  app.overview = overview;
  if (before !== key(overview)) { drawNav(); drawMode(); }
  drawWaiting();
}
function drawNav() {
  const here = (location.hash.slice(2).split("?")[0] || "overview").split("/")[0];
  $("#nav").replaceChildren(...Object.entries(VIEWS).map(([id, v]) => el("a", { href: "#/" + id, "aria-current": id === here ? "page" : null },
    el("span", {}, v.icon), v.name, id === "review" && app.overview && app.overview.review ? el("span", { class: "count" }, app.overview.review) : null)));
}
// A question can wait for hours: say so in the tab title, in a banner on every page, and (if allowed) as a notification.
let notified = "";
function drawWaiting() {
  const w = app.overview && app.overview.waiting;
  document.title = w ? "❓ bdnex: waiting for you" : "bdnex";
  let bar = $("#waiting");
  if (!w) { if (bar) bar.remove(); notified = ""; return; }
  if (!bar) { bar = el("div", { id: "waiting", class: "waiting", role: "status" }); document.body.prepend(bar); }
  bar.replaceChildren(el("span", {}, "A task is waiting for your answer: " + w.message + " "), el("a", { class: "btn", href: "#/tasks?job=" + w.job }, "Answer"));
  const key = w.job + ":" + w.question;
  if (key !== notified && document.hidden && "Notification" in window && Notification.permission === "granted") {
    try { new Notification("bdnex needs you", { body: w.message }); } catch (e) { /* some phones only allow it from a service worker */ }
  }
  notified = key;
}
setInterval(() => { if (!document.hidden || (app.overview && app.overview.busy)) refreshBadge(); }, 4000);

async function logout() {
  try { await fetch("/api/logout", { method: "POST", headers: { "X-Requested-With": "bdnex" } }); } catch (e) { /* the page reloads anyway */ }
  location.reload();
}
function drawMode() {
  const o = app.overview;
  $("#mode").replaceChildren(...[o ? el("b", { class: o.allow_changes ? "on" : "" }, o.allow_changes ? "changes allowed" : "read-only") : null,
    o && o.busy ? el("div", {}, "a job is running") : null,
    o && o.watch && o.watch.enabled ? el("div", { title: "Settings: watch.enabled" }, `watching the sources every ${o.watch.every} min`) : null,
    o && "Notification" in window && window.isSecureContext && Notification.permission === "default"
      ? el("div", {}, el("button", { onclick: () => Notification.requestPermission().then(drawMode) }, "Notify me when a question waits")) : null,
    o && o.login_required ? el("div", {}, el("button", { onclick: logout }, "Log out")) : null].filter(Boolean));
}
async function route() {
  const [path, query] = location.hash.slice(2).split("?");
  const id = (path || "overview").split("/")[0];
  const params = new URLSearchParams(query || "");
  const entry = VIEWS[id] || VIEWS.overview;
  drawNav(); loading();
  try {  // a link such as #/albums?q=series:nains or #/series?name=nains opens the view already filtered
    if (id === "albums") await entry.draw(params.get("q") || "");
    else if (id === "series") await entry.draw(params.get("name") || "", params.get("missing") === "1");
    else if (id === "files" || id === "people") await entry.draw(params.get("q") || "");
    else if (id === "tasks") await entry.draw(params.get("job"));
    else await entry.draw();
  } catch (e) { failure(e); }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
(async () => { try { app.overview = await api("/api/overview"); } catch (e) { /* the view shows it */ } drawNav(); drawMode(); route(); })();
