// ═══════════════════════════════════════════════════════════════════════════
// drill.js — THE drill panel. One implementation, every section.
//
// v1 shipped two: a plain rows table (Today/Pipeline/Clients) and a much richer
// Money one with bucket pills, a "where it went" who-strip, search-within and a
// biggest/newest sort. They drifted. This is the richer one, generalised — the
// extra machinery only appears when a caller supplies the hook that powers it.
//
// THE TRACEABILITY CONTRACT (brief §0 rule 2) is enforced here, not by callers:
//   * the header states the TRUE total the server reported, and
//   * ADMITS when the list is capped ("showing the latest 300"), and
//   * the sum shown is always the sum of the rows actually rendered, labelled
//     as such, so a filtered view can never be mistaken for the whole.
//
// USAGE
//   import { openDrill } from "../drill.js";
//   openDrill({ title: "Fresh hot leads", rows, columns, total: res.total });
//
// There is no `host` argument: the panel is a single app-level sheet. Escape
// closes it, the backdrop closes it, focus returns to whatever opened it.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import { el, append, clear, fmt, icon, table as uiTable, emptyState, btn } from "./ui.js";

const ROW_CAP = 300;          // rendered rows; the header always states the truth

let _panel = null, _backdrop = null, _opener = null, _escBound = false;

function mount() {
  if (_panel) return;
  _backdrop = el("div", { class: "drill-backdrop", on: { click: () => closeDrill() } });
  _panel = el("div", {
    class: "drill-panel",
    attrs: { role: "dialog", "aria-modal": "true", "aria-label": "Details" },
  });
  document.body.append(_backdrop, _panel);
  if (!_escBound) {
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isOpen()) { e.stopPropagation(); closeDrill(); }
    });
    _escBound = true;
  }
}

export function isOpen() { return !!_panel && _panel.classList.contains("show"); }

export function closeDrill() {
  if (!_panel) return;
  _panel.classList.remove("show");
  _backdrop.classList.remove("show");
  setTimeout(() => { if (!isOpen()) { clear(_panel); _backdrop.style.display = "none"; } }, 200);
  if (_opener && document.contains(_opener)) { try { _opener.focus(); } catch (e) { /* ignore */ } }
  _opener = null;
}

/**
 * openDrill(spec)
 *
 * REQUIRED
 *   title    string                 what this figure is
 *   rows     array                  the rows the server returned
 *   columns  [{key,label,num?,render?(row)}]   see ui.table
 *
 * TRACEABILITY
 *   total       number   the TRUE row count behind the figure. Defaults to
 *                        rows.length; pass the server's `total` whenever it
 *                        can exceed the returned page, or the drill will lie.
 *   note        Node|str an extra caveat line under the header. It is rendered
 *                        even when there are NO rows — a caveat on an empty
 *                        drill is exactly when the caveat matters most.
 *   capNote     string   REPLACES the default "…newest first" explanation of
 *                        the cap. The default assumes the server truncated by
 *                        TIME; say so here when it did not (a top-N-by-count
 *                        list is truncated by rank, and calling that "the
 *                        latest N" is a lie about which rows are missing).
 *
 * OPTIONAL POWER (each one lights up a piece of the panel)
 *   amount(row) → number   enables: header sum, facet sums, biggest-first sort,
 *                          the running "N shown · $X" subtotal
 *   time(row)   → string   enables: newest-first sort (ISO-ish strings compare)
 *   search      true | (row)→string    enables the search-within box
 *   facets      [{label, allLabel, of(row)→key|null}]
 *                          pill strips, biggest group first, click to narrow.
 *                          Facet 2+ regroups inside the current facet-1 pick,
 *                          which is what makes the money "who-strip" useful.
 *   defaultSort "biggest" | "newest"   (default: biggest when the total is
 *                          negative — i.e. a cost drill — else newest)
 *   emptyText   string     shown when rows is empty
 *   maxFacet    number     pills per strip (default 9)
 *   rowCap      number     rendered rows (default 300)
 *   subtitle    string     small line under the title
 */
export function openDrill(spec) {
  mount();
  _opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  const rows = spec.rows || [];
  const columns = spec.columns || [];
  const total = spec.total == null ? rows.length : spec.total;
  const capped = total > rows.length;
  const cap = spec.rowCap || ROW_CAP;
  const amount = typeof spec.amount === "function" ? spec.amount : null;
  const timeOf = typeof spec.time === "function" ? spec.time : null;
  const searchOf = spec.search === true
    ? (r) => Object.values(r).map((v) => (v == null ? "" : String(v))).join(" ")
    : (typeof spec.search === "function" ? spec.search : null);
  const facets = (spec.facets || []).filter((f) => typeof f.of === "function");
  const maxFacet = spec.maxFacet || 9;

  const sumOf = (list) => (amount ? list.reduce((s, r) => s + (Number(amount(r)) || 0), 0) : null);
  const grandSum = sumOf(rows);

  // ── header ───────────────────────────────────────────────────────────────
  const countBits = [
    `${fmt.num(total)} row${total === 1 ? "" : "s"}`,
  ];
  if (amount) countBits.push(`these rows sum to ${fmt.usd2(grandSum)}`);
  const countLine = el("span", "count", countBits.join(" · ") + " ");
  if (capped) {
    // "the latest N" is a claim about ORDER. Only make it when the caller has
    // not told us the cut was made some other way.
    append(countLine, el("span", "capped", spec.capNote
      ? `— showing ${fmt.num(rows.length)} of them`
      : `— showing the latest ${fmt.num(rows.length)}`));
  }

  clear(_panel);
  _panel.appendChild(el("div", "drill-head", [
    el("div", "grow", [
      el("h4", null, spec.title || "Details"),
      countLine,
      spec.subtitle ? el("div", "small muted", spec.subtitle) : null,
    ]),
    el("button", {
      class: "btn-icon ghost", type: "button",
      attrs: { "aria-label": "Close details (Esc)", title: "Close (Esc)" },
      on: { click: () => closeDrill() },
    }, icon("x", { size: 16 })),
  ]));

  if (!rows.length) {
    // The caveat is rendered HERE too. An empty drill with a hidden note is
    // the worst case: "0 rows" reads as a fact when the note may be saying
    // the read was gated, aggregated or capped to nothing.
    append(_panel, el("div", "drill-body", [
      spec.note ? el("div", "drillsub", spec.note) : null,
      emptyState(spec.emptyText || "No rows behind this figure.", { icon: "inbox" }),
    ]));
    show();
    return;
  }

  // ── state ────────────────────────────────────────────────────────────────
  const picks = facets.map(() => null);
  let needle = "";
  let sortAmount = spec.defaultSort
    ? spec.defaultSort === "biggest"
    : (amount ? grandSum < 0 : false);

  const controls = el("div", "drill-controls");
  const body = el("div", "drill-body");
  _panel.append(controls, body);

  // rows surviving facets 0..i-1 (so each strip regroups inside the one above)
  const upTo = (i) => rows.filter((r) =>
    facets.every((f, j) => j >= i || picks[j] == null || f.of(r) === picks[j]));

  function pillStrip(items, activeKey, onPick, allLabel) {
    const strip = el("div", "drillstrip");
    const mk = (label, key, sum, n, extra) => {
      const b = el("button", {
        class: "gpill" + (extra || "") + (activeKey === key ? " on" : ""),
        type: "button",
        attrs: { "aria-pressed": String(activeKey === key) },
        on: { click: () => onPick(activeKey === key ? null : key) },
      }, [
        String(label) + " ",
        sum != null ? el("span", "gsum", fmt.usd(sum)) : null,
        n != null ? el("span", "gn", " ×" + n) : null,
      ]);
      return b;
    };
    strip.appendChild(mk(allLabel, null,
      amount ? items.reduce((s, g) => s + g.sum, 0) : null,
      items.reduce((s, g) => s + g.n, 0), " all"));
    items.forEach((g) => strip.appendChild(mk(g.label, g.key, amount ? g.sum : null, g.n)));
    return strip;
  }

  function groupsFor(i) {
    const src = upTo(i);
    const map = new Map();
    src.forEach((r) => {
      const k = facets[i].of(r);
      if (k == null || k === "") return;
      const g = map.get(k) || { key: k, label: facets[i].labelOf ? facets[i].labelOf(k) : k, sum: 0, n: 0 };
      g.sum += amount ? (Number(amount(r)) || 0) : 0;
      g.n += 1;
      map.set(k, g);
    });
    return [...map.values()]
      .sort((a, b) => (amount ? Math.abs(b.sum) - Math.abs(a.sum) : b.n - a.n))
      .slice(0, maxFacet);
  }

  function visibleRows() {
    const nl = needle.trim().toLowerCase();
    let out = rows.filter((r) =>
      facets.every((f, j) => picks[j] == null || f.of(r) === picks[j])
      && (!nl || !searchOf || String(searchOf(r) || "").toLowerCase().includes(nl)));
    if (amount && sortAmount) {
      out = out.slice().sort((a, b) => Math.abs(Number(amount(b)) || 0) - Math.abs(Number(amount(a)) || 0));
    } else if (timeOf) {
      out = out.slice().sort((a, b) => (String(timeOf(a) || "") < String(timeOf(b) || "") ? 1 : -1));
    }
    return out;
  }

  function renderControls() {
    clear(controls);
    facets.forEach((f, i) => {
      const gs = groupsFor(i);
      if (gs.length < 2) return;           // a one-group strip explains nothing
      controls.appendChild(pillStrip(gs, picks[i], (k) => {
        picks[i] = k;
        for (let j = i + 1; j < picks.length; j++) picks[j] = null;
        renderControls(); renderRows();
      }, f.allLabel || ("All " + (f.label || "groups"))));
    });
    if (searchOf || amount) {
      const bar = el("div", "field-row");
      if (searchOf) {
        const q = el("input", {
          type: "search", value: needle, placeholder: "search within…",
          attrs: { "aria-label": "search within these rows" },
          on: { input: (e) => { needle = e.target.value; renderRows(); } },
        });
        bar.appendChild(q);
      }
      if (amount) {
        const st = btn(sortAmount ? "sort: biggest" : "sort: newest", () => {
          sortAmount = !sortAmount;
          st.textContent = sortAmount ? "sort: biggest" : "sort: newest";
          renderRows();
        }, { sm: true });
        bar.appendChild(st);
      }
      controls.appendChild(bar);
    }
    if (!controls.childNodes.length) controls.style.display = "none";
    else controls.style.display = "";
  }

  function renderRows() {
    const out = visibleRows();
    clear(body);
    if (spec.note) body.appendChild(el("div", "drillsub", spec.note));
    const filtered = out.length !== rows.length;
    const bits = [`${fmt.num(out.length)} shown`];
    if (amount) bits.push(`${fmt.usd2(sumOf(out))}${filtered ? " (this selection)" : ""}`);
    body.appendChild(el("div", "drillsub", bits.join(" · ")));
    body.appendChild(uiTable({
      columns, rows: out.slice(0, cap),
      empty: "Nothing matches that filter — clear it above.",
    }));
    if (out.length > cap) {
      body.appendChild(el("div", "drill-cap",
        `Showing ${cap} of ${fmt.num(out.length)} matching rows — narrow the`
        + ` filters above to see the rest.`));
    }
    if (capped) {
      append(body, el("div", "drill-cap", spec.capNote
        ? spec.capNote
        : `The server capped this read at ${fmt.num(rows.length)} of`
          + ` ${fmt.num(total)} rows, newest first. The count in the header is`
          + ` the true total; the sum covers only the rows above.`));
    }
  }

  renderControls();
  renderRows();
  show();
}

function show() {
  _backdrop.style.display = "";
  requestAnimationFrame(() => {
    _backdrop.classList.add("show");
    _panel.classList.add("show");
    const close = _panel.querySelector(".btn-icon");
    if (close) close.focus();
  });
}

/**
 * drillColumns — column presets shared by more than one section, so two
 * sections can never describe the same row shape differently.
 */
export const cols = {
  /** money/transaction rows: {dt,b,t,s,d,u} as returned by /api/money/transactions */
  txn(BLABEL = {}) {
    return [
      { key: "dt", label: "Date", render: (r) => el("span", "muted", fmt.dateShort(r.dt)) },
      { key: "b", label: "Bucket", render: (r) => el("span", "chip " + (r.b || ""), BLABEL[r.b] || r.b || "—") },
      { key: "t", label: "Type" },
      { key: "s", label: "Source", cls: "muted" },
      { key: "d", label: "Description" },
      { key: "u", label: "Amount", num: true,
        render: (r) => el("span", (r.u >= 0 ? "pos" : "neg"), fmt.usd2(r.u)) },
    ];
  },
};

export default { openDrill, closeDrill, isOpen, cols };
