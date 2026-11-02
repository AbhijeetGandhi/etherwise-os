// ════════════════════════════════════════════════════════════════════════════
// runs.js — "Fetch latest": several systems, one button (2026-11-02).
//
// Shared by Today and Upwork (today.js cannot import upwork.js — upwork.js
// imports today.js — so the chain lives here). Uses ONLY the existing
// allowlisted /api/rerun kick and the /api/upwork/run poll; nothing is added
// to the server's allowlist and nothing here sends anything outward.
// ════════════════════════════════════════════════════════════════════════════
"use strict";
import { el, btn, toast, qstr } from "./ui.js";

const RUN_POLL_MS = 4000;
const RUN_POLL_MAX_MS = 6 * 60 * 1000;      // a paced MCP read is ~3–4 min

/*
 * Abhijeet, on Today's "0 proposals today" after sending two: "I need a way
 * to quick say fetch and it should fetch latest, at least for all Upwork
 * related things." The applied count comes from connects_history, which
 * upwork_financials fills once a day at 06:05 — right, but a day late by the
 * afternoon. This runs the Upwork readers in sequence through the SAME
 * allowlisted /api/rerun + /api/upwork/run polling as a single Run now (no new
 * server surface, nothing added to the allowlist), then refreshes the tab.
 * Sequential on purpose: the readers share the Upwork token's rate budget and
 * the MCP session; one at a time cannot trip the other.
 */
export const UPWORK_FETCH_CHAIN = [
  ["upwork_financials", "applied + money"],
  ["upwork_sync", "proposals + contracts"],
  ["upwork_inbox", "messages"],
  ["upwork_pulse", "offers + invitations"],
  ["upwork_funnel_topup", "the funnel"],
];

export function runChain(ctx, chain, label, opts = {}) {
  const wrap = el("span", "runbtn runchain");
  const status = el("span", "muted small runbtn-status", opts.status || "");
  let busy = false;
  const b = btn(label, async () => {
    if (busy) return;
    busy = true;
    b.disabled = true;
    const results = [];
    for (let i = 0; i < chain.length; i++) {
      const [task, words] = chain[i];
      b.textContent = `${i + 1}/${chain.length} · ${words}…`;
      status.textContent = `running ${task}`;
      // eslint-disable-next-line no-await-in-loop
      const r = await runOne(ctx, task);
      results.push([words, r]);
      if (r.state === "failed-to-start") {
        toast(`${words} could not start: ${r.error || "see the logs"}`, true);
      }
    }
    const bad = results.filter(([, r]) => r.state !== "ok" && r.state !== "empty");
    const okN = results.length - bad.length;
    toast(bad.length
      ? `${label}: ${okN} of ${results.length} fine — ${bad.map(([w, r]) => `${w} ${r.state}`).join(", ")}`
      : `${label}: all ${results.length} read — ${results.map(([w, r]) => r.state === "empty" ? `${w} (nothing new)` : w).join(", ")}`,
      bad.length > 0);
    b.disabled = false; b.textContent = label; status.textContent = ""; busy = false;
    ctx.refresh();
  }, { sm: true, variant: opts.variant || "primary", icon: "refresh-cw",
    title: `reads ${chain.map((c) => c[0]).join(", ")} now, in that order, then refreshes` });
  wrap.append(b, status);
  return wrap;
}

/** kick one task and wait for its run; {state: ok|empty|failed|failed-to-start|timeout, error} */
async function runOne(ctx, task) {
  const before = await ctx.api("/api/upwork/run?" + qstr({ task })).catch(() => null);
  const beforeId = before && before.run ? before.run.id : 0;
  let waitingOnScheduled = !!(before && before.running);
  if (!waitingOnScheduled) {
    let res;
    try { res = await ctx.post("/api/rerun", { task }); } catch (e) { res = { ok: false, error: String(e.message || e) }; }
    if (!res || !res.ok) return { state: "failed-to-start", error: res && res.error };
  }
  const t0 = Date.now();
  while (Date.now() - t0 < RUN_POLL_MAX_MS) {
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, RUN_POLL_MS));
    let now = null;
    // eslint-disable-next-line no-await-in-loop
    try { now = await ctx.api("/api/upwork/run?" + qstr({ task })); } catch (e) { now = null; }
    const run = now && now.run;
    const fresh = run && (waitingOnScheduled ? run.id >= beforeId : run.id > beforeId);
    if (fresh && !now.running) {
      if (run.status === "completed") return { state: "ok" };
      if (run.status === "skipped_empty") return { state: "empty" };
      return { state: run.status || "failed", error: run.error };
    }
  }
  return { state: "timeout" };
}

