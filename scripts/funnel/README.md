# scripts/funnel — the proposal research chain, kept as the nightly refresh's fetchers

The 8 Oct 2026 proposal research was built from these seven scripts in
`var/prop/` (untracked). The nightly funnel refresh
(`modules/upwork/funnel_refresh.py`, through `scripts/upwork_pull_all.py`)
runs them in this order, from the repo root, so they live here now:

1. `fetch.py`          every status list, paged to the end, then a `get` for
                       every proposal not on disk (read-only MCP)
2. `fetch_rooms.py`    the room's birth time for proposals with a room
3. `fetch_jobs.py`     the job post as Upwork shows it now (`find_jobs get`)
4. `build_dataset.py`  one record per proposal, joined with the ledger
5. `features.py`       letter and context features
6. `export_rows.py`    `model.load()` -> `var/prop/rows.json`
7. then `scripts/proposal_funnel_backfill.py --apply` loads the rows.

Their data stays in `var/prop/` (raw reads, logs, the dataset). They pace
their calls, keep out of the hourly sync's and the scan's minutes, and stop
at once on a refused sign-in. Nothing here writes to Upwork.
