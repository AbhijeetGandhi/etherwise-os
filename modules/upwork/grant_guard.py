"""One refresh at a time, and never a second try on a dead grant.

WHY THIS EXISTS (2026-10-04). Upwork cancelled Abhijeet's OAuth grants twice in
ten days — 25 Sep and 30 Sep — and each time lead-gen sat dead until he
noticed on his own: four and a half days the second time. Reading the logs for
both deaths found two things on OUR side that no amount of re-authorizing would
have fixed, and one thing that was never switched on.

1. TWO PROCESSES REFRESHED THE SAME TOKEN IN THE SAME SECOND.
     [06:05:04 IST] upwork_pulse      | run 14321 started
     [06:05:04 IST] upwork_financials | run 14322 started
     [06:05:05 IST] upwork_financials | FAILED ... invalid_grant
     [06:05:06 IST] upwork_pulse      | FAILED ... invalid_grant
   That is the first failure of 25 Sep. launchd starts the :05 pulse and the
   06:05 ledger read on the same tick; both read the same refresh token from
   the file and both presented it. Upwork ROTATES refresh tokens — each one is
   good exactly once — and a second presentation is, to any OAuth server that
   follows the security BCP, a stolen-token replay: the whole grant is revoked.
   There was no lock anywhere in either client. Every re-authorization since
   would have died the same way the next time two jobs shared a tick, and the
   catch-up job routinely kicks five rails within six seconds.

2. A DEAD TOKEN WAS REPLAYED ~100 TIMES A DAY. After a grant died, every rail
   kept presenting the revoked refresh token on every run: sync 223 times,
   the scanner 96, the pulse every fifteen minutes. On 30 Sep the second grant
   died with NO refresh of its own in flight — five minutes after sync's 117th
   replay of the other, already-dead token. Cause not proven; hammering a
   token endpoint with a revoked credential is precisely what abuse detection
   is built to notice, and it buys nothing: the answer cannot change until a
   human re-authorizes.

3. NOBODY WAS TOLD. core/notify.py's interrupt drainer has never been
   scheduled — 2,432 critical anomalies sat undrained. His answer, 2026-10-04:
   "Yes, one email per outage".

So, for both clients (the MCP transport and the GraphQL one):
  * `locked()`   — an exclusive cross-process lock held across read -> POST ->
                   write, so a refresh token is presented by ONE process;
  * the caller re-reads the file under the lock and skips the network entirely
    if another process already refreshed while it waited;
  * `write_atomic()` — the token file is replaced, never truncated-then-written
    (a reader used to be able to catch it empty);
  * `mark_dead()` / `dead()` — a circuit breaker keyed to the FINGERPRINT of the
    refused refresh token. While it stands, nothing calls Upwork with that
    token again. It clears itself the moment the file holds a different
    refresh token, however that token got there, so a re-auth by any route
    reopens the circuit without anyone remembering to;
  * `alert_once()` — one email per grant death, naming the command that fixes
    it. Not one per failed run.

Nothing in this file ever reads, logs or stores a token: the breaker keeps a
truncated SHA-256 of the refresh token, which identifies it and cannot be
turned back into it.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from core import config

LOCK_TIMEOUT_S = 45.0            # the holder's own POST times out at 30s
_DEFAULT_EVENTS = config.VAR_DIR / "upwork-grant-events.jsonl"
EVENTS_PATH = _DEFAULT_EVENTS


def _sidecar(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


# ── the lock ─────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def locked(path: Path, timeout_s: float = LOCK_TIMEOUT_S):
    """Exclusive advisory lock on `<path>.lock`. flock, not a lockfile-exists
    test: the kernel drops it when the holder exits or crashes, so there is no
    stale lock to clean up and no process can be locked out by a dead one.

    A timeout raises TimeoutError, which the callers' own classification reads
    as a NETWORK-kind failure ("nothing to repair, the next run self-heals") —
    deliberately not an auth error: nobody should be sent to re-authorize a
    grant because one refresh was slow."""
    lock_path = _sidecar(path, ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    waited = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, InterruptedError):
                waited = True
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"another process held the token refresh lock on"
                        f" {path.name} for {timeout_s:.0f}s") from None
                time.sleep(0.05)
        if waited:
            note(path, "waited_for_lock")
        yield waited
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def write_atomic(path: Path, text: str) -> None:
    """Replace, never truncate. A crash mid-write used to be able to leave the
    credentials file empty — for the GraphQL client that file also holds the
    client secret, and losing it is a trip back to the Upwork developer portal."""
    tmp = _sidecar(path, f".tmp{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(str(tmp))
        raise
    with contextlib.suppress(OSError):
        os.chmod(str(path), 0o600)


# ── the circuit breaker ──────────────────────────────────────────────────────

def fingerprint(refresh_token) -> str:
    """Identifies a refresh token without being one."""
    return hashlib.sha256(str(refresh_token or "").encode()).hexdigest()[:16]


def dead(path: Path, refresh_token) -> Optional[dict]:
    """The marker, if THIS refresh token has already been refused.

    Keyed to the token, not to the file: a marker left by a grant that has
    since been replaced is stale by definition and is removed here rather than
    obeyed. That is what makes the breaker safe to leave unattended — it can
    block the replay of a dead token and nothing else."""
    marker = _sidecar(path, ".dead")
    try:
        doc = json.loads(marker.read_text())
    except (OSError, ValueError):
        return None
    if doc.get("refresh_fp") != fingerprint(refresh_token):
        clear(path, why="the file holds a different refresh token now")
        return None
    return doc


def mark_dead(path: Path, refresh_token, *, grant: str, reason: str,
              command: str, down: str) -> dict:
    """Record that Upwork refused this refresh token. Idempotent: the first
    refusal sets `since`; later ones (there should be none) leave it alone."""
    existing = dead(path, refresh_token)
    if existing:
        return existing
    doc = {"grant": grant, "since": _now(), "reason": str(reason)[:300],
           "command": command, "down": down,
           "refresh_fp": fingerprint(refresh_token), "alerted_at": None}
    write_atomic(_sidecar(path, ".dead"), json.dumps(doc, indent=2) + "\n")
    note(path, "marked_dead", reason=str(reason)[:200])
    return doc


def clear(path: Path, why: str = "re-authorized") -> None:
    marker = _sidecar(path, ".dead")
    if marker.exists():
        with contextlib.suppress(OSError):
            marker.unlink()
        note(path, "breaker_cleared", why=why)


def refusal_message(doc: dict) -> str:
    """What a rail says while the breaker stands — the same sentence every run,
    with no call to Upwork behind it."""
    return (f"Upwork {doc.get('grant')} grant has been dead since {doc.get('since')}"
            f" ({doc.get('reason')}) — re-authorize with `{doc.get('command')}`."
            f" {doc.get('down')} stay down until then. Not retried: the refused"
            f" token is never presented to Upwork twice.")


# ── one email per death ──────────────────────────────────────────────────────

def _is_live_path(path: Path) -> bool:
    """Only the real credentials directory can ever send an email. A test that
    points a client at a temp file cannot mail Abhijeet by forgetting a mock."""
    try:
        return Path(path).resolve().parent == Path(config.CREDENTIALS_DIR).resolve()
    except OSError:
        return False


def _send(subject: str, html: str) -> None:
    from core import notify
    notify.send_gmail(subject, html)


def alert_once(path: Path, refresh_token) -> str:
    """Email him ONCE per grant death. Returns what happened, never raises —
    an alert that fails must not replace the auth error it is reporting. An
    unsent alert is retried by the next failing run (which still makes no call
    to Upwork), so a dead SMTP delays the email rather than losing it."""
    doc = dead(path, refresh_token)
    if not doc:
        return "no dead grant"
    if doc.get("alerted_at"):
        return "already alerted"
    if not _is_live_path(path):
        return "not the live credentials — no email"
    if config.SHADOW_MODE.get("upwork"):
        return "upwork is in shadow — no email"
    subject = f"Upwork login cancelled — {doc.get('down')} are down"
    html = (f"<p><b>Upwork cancelled the {doc.get('grant')} login at"
            f" {doc.get('since')}.</b></p>"
            f"<p><b>Down until you re-authorize:</b> {doc.get('down')}.</p>"
            f"<p><b>Fix (on the Mac, then click Allow):</b></p>"
            f"<pre>cd ~/Desktop/Etherwise/etherwise-v3\n{doc.get('command')}</pre>"
            f"<p>What Upwork said: <i>{doc.get('reason')}</i></p>"
            f"<p>This is the only email for this outage. Nothing is retrying"
            f" against Upwork in the meantime.</p>")
    try:
        _send(subject, html)
    except Exception as exc:                                # noqa: BLE001
        note(path, "alert_failed", error=repr(exc)[:200])
        return f"alert failed: {exc!r}"
    doc["alerted_at"] = _now()
    with contextlib.suppress(OSError):
        write_atomic(_sidecar(path, ".dead"), json.dumps(doc, indent=2) + "\n")
    note(path, "alerted")
    return "alerted"


# ── forensics ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S IST")


def note(path: Path, event: str, **fields) -> None:
    """One line per refresh / refusal / wait, with the process that did it.

    Both deaths had to be reconstructed from run start times because a refresh
    left no trace anywhere — the only evidence that two processes collided was
    two log lines sharing a second. Next time the file says who refreshed,
    when, and who was waiting. Never a token; never fatal."""
    try:
        # a client pointed at a temp file (every test) must not write into the
        # real forensic log; a test that wants the events redirects EVENTS_PATH
        if EVENTS_PATH == _DEFAULT_EVENTS and not _is_live_path(path):
            return
        rec = {"at": _now(), "file": Path(path).name, "event": event,
               "pid": os.getpid(), "proc": " ".join(sys.argv[:2])[-80:]}
        rec.update(fields)
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_PATH, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:                                       # noqa: BLE001
        pass
