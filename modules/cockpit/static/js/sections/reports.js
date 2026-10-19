// ═══════════════════════════════════════════════════════════════════════════
// sections/reports.js — what gets sent to a client, and the hours behind it.
//
// WHY THIS IS ITS OWN TAB (2026-10-19). Hours shipped first and landed inside
// Clients, and his reaction was the right one: "wasn't the main goal to
// generate EOW report — currently i only see hours". The report was built but
// sat three clicks deep behind a drill, which is the same as not being built.
// The thing you come here to do is now the thing on the page.
//
// WHAT YOU SEE IS WHAT THE CLIENT GETS (same day, second pass). The first
// version showed the report as cockpit KPI tiles + tables + a markdown blob —
// an operator's view of a client document. His verdict: it has to be a real,
// branded, professional report with collapsible hours and a PDF. So the page
// now previews THE document (modules/hours/eow_doc.py) in a frame, and every
// output comes from that one renderer:
//   · Download PDF  headless Chrome prints the print variant server-side (all
//                   workstreams open — a closed <details> prints nothing), a
//                   copy is kept under var/reports/eow/, the bytes come back
//                   here and are saved through the browser.
//   · Print         the preview frame's own print dialog.
//   · Copy as text  the same report as plain text (eow_doc.render_text), for
//                   a Slack message or an email body.
// Nothing is SENT from here — drafts-only is a §0 law.
//
//   · Hours tab     the overview, per-client periods and logging gaps.
// ═══════════════════════════════════════════════════════════════════════════
import { el, clear, btn, card, emptyState, notice, toast, copyText, skeleton } from "../ui.js";
import { subtabs } from "../subtabs.js";

export async function render(root, ctx) {
  await subtabs(root, {
    section: "reports", ctx,
    tabs: [{ id: "eow", label: "Weekly report" }, { id: "hours", label: "Hours" }],
    views: {
      eow: () => { const host = el("div", "stack"); eow(host, ctx); return host; },
      hours: () => {
        const host = el("div", "stack");
        import("./hours.js").then((m) => m.render(host, ctx));
        return host;
      },
    },
  });
}

async function eow(root, ctx) {
  const ov = await ctx.api("/api/hours");
  const clients = (ov.clients || []).filter((c) => c.period);
  if (!clients.length) {
    root.appendChild(emptyState(
      "No client has a readable period yet — run bin/hours-sync.",
      { icon: "file-text" }));
    return;
  }

  // the picker IS the page: client + period, then the report.
  const cSel = el("select", { class: "input",
    attrs: { "aria-label": "client" } },
  clients.map((c) => el("option", { value: c.client_id }, c.client)));
  const pSel = el("select", { class: "input",
    attrs: { "aria-label": "period" } }, []);
  const out = el("div", "stack");
  const field = (label, control) => el("div", "rep-field",
    [el("label", null, label), control]);
  const buildBtn = btn("Build report", () => build(), { variant: "primary" });

  root.append(card("Weekly client report", el("div", "stack", [
    el("div", "rep-controls", [field("Client", cSel), field("Period", pSel), buildBtn]),
    el("div", "small muted",
      "Previews the exact document the client receives. Nothing is sent from"
      + " here — download the PDF or copy the text and send it yourself."),
  ])), out);

  async function loadPeriods() {
    const d = await ctx.api("/api/hours/client?client="
      + encodeURIComponent(cSel.value));
    clear(pSel);
    (d.periods || []).slice().reverse().forEach((p) => pSel.appendChild(
      el("option", { value: p.list_id }, `${p.name}`)));
  }
  cSel.addEventListener("change", () => { loadPeriods(); clear(out); });
  pSel.addEventListener("change", () => clear(out));
  await loadPeriods();
  build();   // the latest period of the first client, so the tab is never blank

  async function build() {
    const client = cSel.value, period = pSel.value;
    clear(out).appendChild(el("div", "rep-stage rep-loading", [skeleton({ lines: 8 })]));
    buildBtn.disabled = true;
    let r;
    try {
      r = await ctx.api(`/api/hours/eow?client=${encodeURIComponent(client)}`
        + `&period=${encodeURIComponent(period)}&fmt=doc`);
    } catch (e) {
      clear(out).appendChild(notice("bad", `Could not build the report: ${e.message}`));
      return;
    } finally {
      buildBtn.disabled = false;
    }
    clear(out);
    if (r.error) { out.appendChild(notice("bad", r.error)); return; }

    const sprint = String(r.period || "").split(" (")[0];
    const pdfBtn = btn("Download PDF", () => downloadPdf(ctx, pdfBtn, client, period),
      { variant: "primary", icon: "download" });
    const bar = el("div", "rep-bar", [
      el("div", "rep-bar-meta", [
        el("span", "rep-bar-title", `${r.client_name} · ${sprint}`),
        el("span", "rep-bar-sub",
          `${r.open ? "In progress" : "Period closed"} · figures as of ${asOf(r.as_of)}`),
      ]),
      el("div", "rep-bar-actions", [
        btn("Copy as text", (ev) => copyText(r.text || "", ev.currentTarget),
          { icon: "copy" }),
        btn("Print", () => printFrame(frame), { icon: "printer" }),
        pdfBtn,
      ]),
    ]);

    // the document in a frame: its own CSS cannot leak into the cockpit and the
    // cockpit's dark theme cannot leak into it — the client's page, unaltered
    const frame = el("iframe", { class: "rep-frame",
      attrs: { title: `${r.client_name} weekly report preview` } });
    frame.srcdoc = r.html;
    frame.addEventListener("load", () => fitFrame(frame));

    out.append(bar, el("div", "rep-stage", [frame]));
  }
}

/** "2026-10-19 17:54:02" (IST, from the sync) → "19 Oct 2026, 17:54 IST" —
 *  the same words the document's footer uses for the same stamp. */
function asOf(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(stamp || "");
  if (!m) return stamp || "—";
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
    "Oct", "Nov", "Dec"][Number(m[2]) - 1];
  return `${Number(m[3])} ${mon} ${m[1]}, ${m[4]}:${m[5]} IST`;
}

/** Grow the frame to its document's height, and keep it there as workstreams
 *  open and close — a scrollbar inside a scrolling page is two scrollbars. */
function fitFrame(frame) {
  const doc = frame.contentDocument;
  if (!doc) return;
  const fit = () => { frame.style.height = doc.documentElement.scrollHeight + "px"; };
  fit();
  if ("ResizeObserver" in window) new ResizeObserver(fit).observe(doc.body);
  doc.addEventListener("toggle", fit, true);   // <details> toggle does not bubble
}

function printFrame(frame) {
  const w = frame.contentWindow;
  if (!w) { toast("Preview is not ready yet.", true); return; }
  w.focus();
  w.print();   // the document's own beforeprint opens every workstream
}

async function downloadPdf(ctx, button, client, period) {
  // children, not textContent: the button carries an icon, and restoring a
  // saved textContent would delete it (the flashCopied bug in ui.js)
  const prev = Array.from(button.childNodes);
  button.disabled = true;
  button.replaceChildren("Rendering PDF…");
  try {
    const d = await ctx.post("/api/hours/eow_pdf", { client, period });
    if (!d.ok) throw new Error(d.error || "PDF failed");
    const bin = atob(d.pdf_b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const a = el("a", { attrs: { href: url, download: d.filename } });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast(`Saved ${d.filename} (${Math.round(d.bytes / 1024)} KB). A copy is kept in`
      + " var/reports/eow/.");
  } catch (e) {
    toast(`PDF failed: ${e.message}`, true);
  } finally {
    button.disabled = false;
    button.replaceChildren(...prev);
  }
}
