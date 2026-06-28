-- 010 payroll (Phase 5) — PREP-ONLY run records. The system computes the run,
-- renders slips, and records what WOULD be paid; it NEVER disburses (no payout
-- path, no bank rail). All money is integer paise. doctor #6 reconciles these:
-- per line Σcomponents == gross and gross−(TDS+PT) == net; per run Σnet == net.
CREATE TABLE IF NOT EXISTS payroll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,                 -- YYYY-MM
    pay_date TEXT NOT NULL,               -- last working day of the month (IST)
    headcount INTEGER NOT NULL,
    gross_minor INTEGER NOT NULL,
    tds_minor INTEGER NOT NULL,
    pt_minor INTEGER NOT NULL,
    net_minor INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'prepared',   -- prepared; NEVER 'disbursed' (manual)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(period)
);

CREATE TABLE IF NOT EXISTS payroll_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
    employee_id TEXT NOT NULL,
    name TEXT,
    gross_minor INTEGER NOT NULL,
    basic_minor INTEGER NOT NULL,
    hra_minor INTEGER NOT NULL,
    special_minor INTEGER NOT NULL,
    lta_minor INTEGER NOT NULL,
    tds_minor INTEGER NOT NULL,
    pt_minor INTEGER NOT NULL,
    net_minor INTEGER NOT NULL,
    UNIQUE(run_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_payroll_lines_run ON payroll_lines(run_id);
