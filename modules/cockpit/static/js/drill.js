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

// ── the close animation's teardown timer ─────────────────────────────────────
// THE DEFECT THIS EXISTS FOR (2026-08-13): closeDrill() scheduled the teardown
// (empty the panel, hide the backdrop) 200ms later and kept no handle, so
// nothing could cancel it. Its guard — `if (!isOpen())` — reads a class that
// show() applies inside a requestAnimationFrame callback, which makes it a
// check-then-act race. Open a new drill inside that 200ms and, if the main
// thread is busy enough that the rAF lands after the timer (these drills render
// 300-row tables, so it routinely is), the timer empties the panel that was
// just built and sets the backdrop to display:none. What is left is an opaque
// 920px sheet at z-index 51 with pointer-events:auto, no children — no close
// button — and a backdrop that cannot be clicked. There is NO WAY OUT WITH A
// MOUSE; only Escape or a section change recovers.
//
// Two guards, because there are two directions to the race:
//   _closeTimer  — cancelled by mount(), so an open always wins over a pending
//                  teardown instead of racing it.
//   _showGen     — bumped by every open AND every close, so a rAF from a
//                  superseded show() cannot re-add .show to a closed panel.
let _closeTimer = null, _showGen = 0;

function cancelTeardown() {
  if (_closeTimer !== null) { clearTimeout(_closeTimer); _closeTimer = null; }
}

function mount() {
  cancelTeardown();
  // isConnected, not just truthiness: a panel that was removed from the DOM
  // (a section that cleared document.body's tail, a hot reload) left `_panel`
  // pointing at a detached node, and every later openDrill() then rendered
  // into nothing at all — a click that silently does zero.
  if (_panel && _panel.isConnected && _backdrop && _backdrop.isConnected) return;
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
  cancelTeardown();                 // never stack two teardowns
  _showGen += 1;                    // abandon any show() rAF still in flight
  _panel.classList.remove("show");
  _backdrop.classList.remove("show");
  const mine = _showGen;
  _closeTimer = setTimeout(() => {
    _closeTimer = null;
    // `mine !== _showGen` means an open (or another close) happened after this
    // teardown was scheduled and mount() somehow did not cancel it. Belt to
    // isOpen()'s braces: isOpen() reads a class that is applied a frame late,
    // so it alone cannot tell "closed" from "opening".
    if (mine !== _showGen || isOpen() || !_panel) return;
    clear(_panel);
    _backdrop.style.display = "none";
  }, 200);
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
 *   truncated   true     "these rows are a cut, and I do NOT know the true
 *                        total". `total > rows.length` was the only way to
 *                        admit a cap, so a reader that caps at 40 and counts
 *                        nothing beyond it (system.anomalies) had no way to
 *                        say so and its drill read as the whole truth. Pair it
 *                        with `capNote` to say what the cut was made ON.
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
 *   wide        true       widen the sheet. For drills whose ROW is a story
 *                          (seven columns of provenance) rather than a record.
 *                          Opt-in: the default side-sheet width is right for
 *                          the other twenty drills.
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
  // `truncated` covers the case `total > rows.length` cannot: the server cut
  // the list and never counted what it left behind, so there is no true total
  // to compare against.
  const unknownTotal = spec.truncated === true && total <= rows.length;
  const capped = total > rows.length || unknownTotal;
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
    unknownTotal
      ? `${fmt.num(rows.length)} row${rows.length === 1 ? "" : "s"} (the true total is not measured)`
      : `${fmt.num(total)} row${total === 1 ? "" : "s"}`,
  ];
  if (amount) countBits.push(`these rows sum to ${fmt.usd2(grandSum)}`);
  const countLine = el("span", "count", countBits.join(" · ") + " ");
  if (capped) {
    // "the latest N" is a claim about ORDER. Only make it when the caller has
    // not told us the cut was made some other way.
    append(countLine, el("span", "capped", unknownTotal
      ? "— this list is a cut, not the whole set"
      : spec.capNote
        ? `— showing ${fmt.num(rows.length)} of them`
        : `— showing the latest ${fmt.num(rows.length)}`));
  }

  clear(_panel);
  _panel.classList.toggle("wide", spec.wide === true);
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

  /**
   * One facet strip. `all` describes the ROW SET this strip groups over —
   * {n, sum, missing, label} — and is NOT derived from `items`.
   *
   * THE DEFECT THIS SIGNATURE EXISTS FOR (2026-08-13): the "All …" pill used
   * to sum and count the GROUPS beside it. groupsFor() drops every row whose
   * facet key is null/empty and then slices to `maxFacet`, so those two
   * numbers are different sets. Live: the header read "608 rows ·
   * $292,497.73", the All pill directly beneath it read "$291,444 ×604", and
   * clicking that pill rendered 608 rows summing to $292,497.73 — the pill
   * contradicted both the line above it and the view it produced, inside the
   * one component whose docblock promises a stated sum is the sum of the rows
   * rendered. The All pill now counts the rows it will render; what the pills
   * beside it do not cover is stated separately rather than quietly deducted.
   */
  function pillStrip(items, activeKey, onPick, allLabel, all) {
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
    strip.appendChild(mk(allLabel, null, all.sum, all.n, " all"));
    items.forEach((g) => strip.appendChild(mk(g.label, g.key, amount ? g.sum : null, g.n)));
    if (all.missing > 0) {
      // not a button: there is no single key that selects these rows. It is a
      // statement of what the pills above do NOT account for, so that "the
      // pills do not add up to All" is answered on the strip itself.
      strip.appendChild(el("span", {
        class: "gpill-rest",
        attrs: {
          title: `${fmt.num(all.missing)} of these ${fmt.num(all.n)} rows are in`
            + ` no pill above — their ${all.label || "value"} is blank, or their`
            + ` group is outside the ${items.length} biggest shown. They are`
            + ` counted in “${allLabel}”, and they are in the table below.`,
        },
      }, `+${fmt.num(all.missing)} in no pill`));
    }
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
      // the rows this strip groups over — the same set clicking "All" renders
      const src = upTo(i);
      const inPills = gs.reduce((s, g) => s + g.n, 0);
      controls.appendChild(pillStrip(gs, picks[i], (k) => {
        picks[i] = k;
        for (let j = i + 1; j < picks.length; j++) picks[j] = null;
        renderControls(); renderRows();
      }, f.allLabel || ("All " + (f.label || "groups")), {
        n: src.length,
        sum: amount ? sumOf(src) : null,
        missing: src.length - inPills,
        label: f.label,
      }));
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
        : unknownTotal
          ? `The server capped this read at ${fmt.num(rows.length)} rows and did`
            + ` not count what it left behind, so there is no true total to show`
            + ` you here — treat this list as a sample, not the whole set.`
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
  cancelTeardown();
  _backdrop.style.display = "";
  const mine = ++_showGen;
  requestAnimationFrame(() => {
    if (mine !== _showGen) return;      // closed (or reopened) before we painted
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
