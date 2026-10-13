"""Read-only census of every proposal through the Upwork MCP, for the proposal research
(Abhijeet, 8 Oct 2026: "fetch all proposals"). Two phases, both resumable:

  1. list  every status list, paged to the end (10 a page)  -> var/prop/raw/list/<Status>_<nnn>.json
  2. get   every distinct proposal id, newest first          -> var/prop/raw/get/<id>.json

PACE: one call every PACE_S seconds - under the ~12 a minute the server allowed in August - and
silent during :44-:46, when the hourly sync reads the same tool. Stops at once on anything that
smells of auth (a dead grant is never replayed); backs off on a rate limit; never loops tight.
Nothing is written to the ledger. Nothing is sent. Tokens are never read or printed here - the
session takes care of them under the grant lock.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from core import config                                    # noqa: E402
from modules.upwork.feeds import mcp_client                # noqa: E402

RAW = Path("var/prop/raw")
LIST, GET = RAW / "list", RAW / "get"
PACE_S = 6.0
STATUSES = ("Accepted", "Offered", "Activated", "Hired", "Declined", "Withdrawn", "Pending", "Archived")
TOOL = "list_freelancer_proposals"
LOGF = open("var/prop/fetch.log", "a", buffering=1)
_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
MAX_GETS = int(_ARGS[0]) if _ARGS else 100000
# the hourly top-up (2026-10-13): no list is read; the proposals the sync has
# stored and the funnel has not are added to the census and read with a `get`
NEW_ONLY = "--new-only" in sys.argv[1:]
_last = [0.0]
calls = [0]


class Stop(Exception):
    pass


def log(m):
    LOGF.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}\n")


def quiet_window():
    """The hourly sync reads this same tool at :45 - stay out of its way."""
    while time.localtime().tm_min in (44, 45, 46):
        time.sleep(20)


def call(session, args):
    waits = (60, 180, 300)
    net = 0
    attempt = 0
    while True:
        quiet_window()
        gap = PACE_S - (time.monotonic() - _last[0])
        if gap > 0 and _last[0]:
            time.sleep(gap)
        try:
            calls[0] += 1
            return session.call(TOOL, args)
        except mcp_client.MCPAuthError as exc:
            raise Stop(f"auth: {str(exc)[:200]}")
        except Exception as exc:                           # noqa: BLE001
            text = repr(exc).lower()
            if any(k in text for k in ("mcp 401", "mcp 403", "invalid_grant", "unauthor", "forbidden")):
                raise Stop(f"auth-like refusal, not retried: {str(exc)[:200]}")
            if "rate limit" in text or "429" in text or "too many" in text:
                if attempt < len(waits):
                    log(f"rate-limited; waiting {waits[attempt]}s")
                    time.sleep(waits[attempt])
                    attempt += 1
                    continue
                raise Stop("rate-limited four times in a row")
            if isinstance(exc, (OSError, TimeoutError)) or "timed out" in text or "urlopen" in text:
                net += 1
                if net <= 3:
                    log(f"network: {str(exc)[:120]}; waiting 45s")
                    time.sleep(45)
                    continue
                raise Stop(f"network down: {str(exc)[:160]}")
            raise
        finally:
            _last[0] = time.monotonic()


def nodes_of(res):
    vp = ((res.get("data") or {}).get("vendorProposals") or res.get("vendorProposals") or {})
    edges = vp.get("edges") or []
    return [(e.get("node") or e) for e in edges if isinstance(e, dict)], (vp.get("pageInfo") or {}), vp.get("totalCount")


def phase_list(s, org):
    index = {}
    for st in STATUSES:
        cursor, page, total = None, 0, None
        while True:
            page += 1
            f = LIST / f"{st}_{page:03d}.json"
            if f.exists():
                res = json.loads(f.read_text())
            else:
                params = {"status": st, "sort_field": "CREATEDDATETIME", "sort_order": "DESC", "limit": 10}
                if cursor:
                    params["cursor"] = cursor
                res = call(s, {"action": "list", "org_uid": org, "params": params})
                f.write_text(json.dumps(res))
            nodes, pi, total = nodes_of(res)
            for n in nodes:
                if not n.get("id"):
                    continue
                e = index.setdefault(str(n["id"]), {"lists": [], "node": n})
                if st not in e["lists"]:
                    e["lists"].append(st)
            # an EMPTY page is not the end: the server now removes, page by page, the rows whose
            # own status is not the one asked for ("filtered_note"), so a page can hold nothing
            # while the list goes on. The end is: no next page, or a cursor that stopped moving.
            if not pi.get("hasNextPage") or not pi.get("endCursor") or pi.get("endCursor") == cursor:
                break
            cursor = pi["endCursor"]
            if page >= 150:
                log(f"list {st}: page cap 150 reached")
                break
            if page % 10 == 0:
                log(f"list {st}: page {page}, distinct so far {len(index)}")
        log(f"list {st}: {page} pages, upstream total {total}, distinct so far {len(index)}")
    return index


def created_of(node):
    a = (node.get("auditDetails") or {}).get("createdDateTime") or {}
    return str(a.get("displayValue") or a.get("rawValue") or "")


def ledger_entry(pid, created, ustatus, lst):
    """A census entry for a proposal only the ledger knows (no list read)."""
    return {"lists": [], "ledger_only": True, "node": {
        "id": str(pid), "auditDetails": {"createdDateTime": {"displayValue": created or ""}},
        "status": {"status": ustatus}, "ledger_list": lst}}


def phase_get(s, org, index, only=None):
    ids = sorted(index if only is None else [i for i in only if i in index],
                 key=lambda i: created_of(index[i]["node"]), reverse=True)
    done = fails = streak = 0
    for pid in ids:
        f = GET / f"{pid}.json"
        if f.exists():
            continue
        if done >= MAX_GETS:
            log(f"get: stopping at the cap of {MAX_GETS} for this run")
            break
        try:
            res = call(s, {"action": "get", "org_uid": org, "params": {"id": pid}})
        except Stop:
            raise
        except Exception as exc:                           # noqa: BLE001
            fails += 1
            streak += 1
            with open("var/prop/get_errors.jsonl", "a") as ef:
                ef.write(json.dumps({"id": pid, "error": str(exc)[:300]}) + "\n")
            if streak >= 8:
                raise Stop(f"8 gets failed in a row; last: {str(exc)[:160]}")
            continue
        streak = 0
        f.write_text(json.dumps(res))
        done += 1
        if done % 25 == 0:
            log(f"get: {done} fetched this run, {fails} failed, {len(list(GET.glob('*.json')))}/{len(ids)} on disk")
    log(f"get: finished this run - {done} fetched, {fails} failed, {len(list(GET.glob('*.json')))}/{len(index)} on disk")


def main_new_only():
    """Add the new proposals to the census (every entry already there is kept,
    so the lists the nightly run read stay in force) and `get` only those."""
    import sqlite3
    from modules.upwork.funnel_refresh import new_proposal_ids
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=20)
    ids = new_proposal_ids(c)
    led = {str(r[0]): r[1:] for r in c.execute(
        "SELECT id, created_dt, upwork_status, mcp_list_status FROM proposals")}
    c.close()
    path = Path("var/prop/census.json")
    index = json.loads(path.read_text()) if path.exists() else {}
    added = [pid for pid in ids if pid not in index]
    for pid in added:
        index[pid] = ledger_entry(pid, *led[pid])
    path.write_text(json.dumps(index))
    log(f"START new-only: {len(ids)} new proposal(s), {len(added)} added to the census of {len(index)}")
    if not ids:
        log("DONE nothing new")
        return
    s = mcp_client.MCPSession()
    s.initialize()
    org = config.UPWORK_ORGS["personal"]
    try:
        phase_get(s, org, index, only=ids)
        log(f"DONE calls {calls[0]}")
    except Stop as exc:
        log(f"STOPPED: {exc} (calls {calls[0]})")


def main():
    if NEW_ONLY:
        return main_new_only()
    log(f"START pace {PACE_S}s, max gets {MAX_GETS}")
    s = mcp_client.MCPSession()
    s.initialize()
    org = config.UPWORK_ORGS["personal"]
    try:
        index = phase_list(s, org)
        listed = len(index)
        import sqlite3
        c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=20)
        for pid, created, ustatus, lst in c.execute(
                "SELECT id, created_dt, upwork_status, mcp_list_status FROM proposals"):
            if str(pid) not in index:
                index[str(pid)] = ledger_entry(pid, created, ustatus, lst)
        c.close()
        Path("var/prop/census.json").write_text(json.dumps(index))
        log(f"census: {listed} listed today + {len(index) - listed} only the ledger knows = {len(index)} proposals")
        phase_get(s, org, index)
        log(f"DONE calls {calls[0]}")
    except Stop as exc:
        log(f"STOPPED: {exc} (calls {calls[0]})")
    except Exception as exc:                               # noqa: BLE001
        log(f"CRASHED: {type(exc).__name__}: {str(exc)[:300]} (calls {calls[0]})")
        raise


if __name__ == "__main__":
    main()
