"""Read-only: for every proposal the ledger holds no room for, ask Upwork whether a room exists
(`list_freelancer_proposals get_room`). A room on a cold proposal means the client opened a conversation -
Upwork's "interview". -> var/prop/raw/room/<proposal id>.json ({"not_found": true} when there is none).
Newest first, one call every 6 s, silent during :44-:46 (the hourly sync uses this tool). Resumable.
Stops at once on anything that smells of auth. Nothing written to the ledger, nothing sent."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts/funnel")
from core import config                                    # noqa: E402
from modules.upwork.feeds import mcp_client                # noqa: E402
import model                                               # noqa: E402

OUT = Path("var/prop/raw/room")
LOGF = open("var/prop/fetch_rooms.log", "a", buffering=1)


def log(m):
    LOGF.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}\n")


rows = sorted(model.load(), key=lambda r: r["created"], reverse=True)
# 07:20 IST 8 Oct: the proposal's own `get` answer carries `room_id` whenever a room exists (agreed with
# get_room on 152 of 152 checked, and with every ledger room), so only the proposals WITH a room are
# asked from here on - for the room's creation time (an invitation's room is born with the proposal;
# a cold proposal's room is born when the client writes).
import glob as _glob
HAS_ROOM = set()
for _f in _glob.glob("var/prop/raw/get/*.json"):
    try:
        if json.loads(Path(_f).read_text()).get("room_id"):
            HAS_ROOM.add(Path(_f).stem)
    except ValueError:
        pass
todo = [r for r in rows if r["id"] in HAS_ROOM and not (OUT / f"{r['id']}.json").exists()]
log(f"START (rooms only) {len(todo)} proposals with a room to read, of {len(HAS_ROOM)} that have one")
s = mcp_client.MCPSession()
s.initialize()
org = config.UPWORK_ORGS["personal"]
done = found = errs = streak = 0
last = 0.0
for r in todo:
    while time.localtime().tm_min in (44, 45, 46):
        time.sleep(20)
    gap = 6.0 - (time.monotonic() - last)
    if gap > 0 and last:
        time.sleep(gap)
    try:
        res = s.call("list_freelancer_proposals", {"action": "get_room", "org_uid": org, "params": {"proposal_id": r["id"]}})
        streak = 0
    except mcp_client.MCPAuthError as exc:
        log(f"STOPPED: auth: {str(exc)[:160]}")
        break
    except Exception as exc:                               # noqa: BLE001
        text = str(exc)
        low = text.lower()
        if "not_found" in low:
            res = {"not_found": True}
            streak = 0
        elif any(k in low for k in ("mcp 401", "mcp 403", "invalid_grant", "unauthor", "forbidden")):
            log(f"STOPPED: auth-like refusal: {text[:160]}")
            break
        elif "rate limit" in low or "429" in low or "too many" in low:
            log("rate-limited; waiting 120s")
            time.sleep(120)
            last = time.monotonic()
            continue
        else:
            errs += 1
            streak += 1
            with open("var/prop/room_errors.jsonl", "a") as ef:
                ef.write(json.dumps({"id": r["id"], "error": text[:300]}) + "\n")
            if streak >= 8:
                log(f"STOPPED: 8 errors in a row; last: {text[:160]}")
                break
            last = time.monotonic()
            continue
    finally:
        last = time.monotonic()
    (OUT / f"{r['id']}.json").write_text(json.dumps(res))
    done += 1
    if not res.get("not_found"):
        found += 1
    if done % 25 == 0:
        log(f"rooms: {done}/{len(todo)} checked, {found} rooms the ledger did not have, {errs} errors")
else:
    log(f"DONE rooms: {done} checked, {found} rooms the ledger did not have, {errs} errors")
