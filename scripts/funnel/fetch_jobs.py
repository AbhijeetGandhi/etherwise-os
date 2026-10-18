"""Read-only: the posting behind every proposal, as Upwork shows it today (`find_jobs get`) - did the
client interview anyone, hire anyone, how did it close, what did they ask for, when was it posted.
-> var/prop/raw/job/<job id>.json. Newest proposals first. Resumable.

PACE: one call every PACE_S seconds, silent around every job-scan slot (the scan uses this same
tool; since 2026-10-18 it runs every 15 minutes, so the window is mcp_search.in_scan_window).
Starts only once the proposals fetch has finished (waits for DONE/STOPPED in var/prop/fetch.log).
Stops at once on anything that smells of auth; backs off on a rate limit. Nothing is written to the
ledger. Nothing is sent."""
import glob
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from core import config                                    # noqa: E402
from modules.upwork.feeds import mcp_client, mcp_search    # noqa: E402

JOB = Path("var/prop/raw/job")
PACE_S = 7.0
LOGF = open("var/prop/fetch_jobs.log", "a", buffering=1)
_last = [0.0]
calls = [0]


class Stop(Exception):
    pass


def log(m):
    LOGF.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}\n")


def quiet_window():
    while mcp_search.in_scan_window(time.localtime().tm_min):
        time.sleep(20)


def call(session, args):
    waits = (60, 180, 300)
    attempt = net = 0
    while True:
        quiet_window()
        gap = PACE_S - (time.monotonic() - _last[0])
        if gap > 0 and _last[0]:
            time.sleep(gap)
        try:
            calls[0] += 1
            return session.call("find_jobs", args)
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


def wait_for_proposals():
    while True:
        try:
            tail = Path("var/prop/fetch.log").read_text().strip().splitlines()[-1]
        except (OSError, IndexError):
            tail = ""
        if " DONE " in tail or "STOPPED" in tail or "CRASHED" in tail:
            log(f"proposals fetch ended: {tail[20:110]}")
            return "STOPPED: auth" not in tail
        time.sleep(30)


def job_ids():
    """(job id, newest proposal date) for every proposal we know, newest first."""
    seen = {}
    census = json.loads(Path("var/prop/census.json").read_text())
    for e in census.values():
        n = e.get("node") or {}
        jid = str(((n.get("marketplaceJobPosting") or {}).get("id")) or "")
        d = str((((n.get("auditDetails") or {}).get("createdDateTime") or {}).get("displayValue")) or "")
        if jid and d > seen.get(jid, ""):
            seen[jid] = d
    for f in glob.glob("var/prop/raw/get/*.json"):
        vp = ((json.loads(Path(f).read_text()).get("data") or {}).get("vendorProposal") or {})
        jid = str(((vp.get("marketplaceJobPosting") or {}).get("id")) or "")
        d = str((((vp.get("auditDetails") or {}).get("createdDateTime") or {}).get("displayValue")) or "")
        if jid and d > seen.get(jid, ""):
            seen[jid] = d
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=20)
    for jid, d in c.execute("SELECT marketplace_job_id, created_dt FROM proposals WHERE marketplace_job_id IS NOT NULL"):
        if str(jid) not in seen:
            seen[str(jid)] = d or ""
    c.close()
    return sorted(seen, key=lambda j: seen[j], reverse=True)


def main():
    log(f"START pace {PACE_S}s - waiting for the proposals fetch")
    if not wait_for_proposals():
        log("STOPPED: the proposals fetch ended on an auth refusal - not starting")
        return
    ids = job_ids()
    log(f"jobs: {len(ids)} distinct, {len(list(JOB.glob('*.json')))} already on disk")
    s = mcp_client.MCPSession()
    s.initialize()
    org = config.UPWORK_ORGS["personal"]
    done = fails = streak = 0
    try:
        for jid in ids:
            f = JOB / f"{jid}.json"
            if f.exists():
                continue
            try:
                res = call(s, {"action": "get", "org_uid": org, "params": {"id": jid}})
            except Stop:
                raise
            except Exception as exc:                       # noqa: BLE001
                fails += 1
                streak += 1
                with open("var/prop/job_errors.jsonl", "a") as ef:
                    ef.write(json.dumps({"id": jid, "error": str(exc)[:300]}) + "\n")
                if streak >= 12:
                    raise Stop(f"12 job reads failed in a row; last: {str(exc)[:160]}")
                continue
            streak = 0
            f.write_text(json.dumps(res))
            done += 1
            if done % 25 == 0:
                log(f"jobs: {done} fetched this run, {fails} failed, {len(list(JOB.glob('*.json')))}/{len(ids)} on disk")
        log(f"DONE jobs: {done} fetched, {fails} failed, {len(list(JOB.glob('*.json')))}/{len(ids)} on disk, calls {calls[0]}")
    except Stop as exc:
        log(f"STOPPED: {exc} (calls {calls[0]}, {done} fetched, {fails} failed)")


if __name__ == "__main__":
    main()
