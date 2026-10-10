"""Pure: model.load() -> var/prop/rows.json (one flat row per proposal, letters and job text included)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts/funnel")
import model                                               # noqa: E402

rows = model.load()
Path("var/prop/rows.json").write_text(json.dumps(rows))
print("rows:", len(rows), "| with job record:", sum(1 for r in rows if r["job_have"]), "| with job text:", sum(1 for r in rows if r["j_desc"]),
      "| rooms:", sum(1 for r in rows if r["room"]), "| kind unsure:", sum(1 for r in rows if not r["kind_sure"]))
