# results/

Outputs committed from real runs. Nothing here is hand-edited; each file is written by the
command next to it (run from the repository root).

| File | Produced by | What it is |
|---|---|---|
| `report.md` | `noc-pulse replay` then `noc-pulse report --markdown results/report.md` | data coverage, per-target KPIs, services vs SLA, hourly ping view, alarms/tickets, SD-WAN table for the overnight run |
| `summary.json` | `noc-pulse report --json results/summary.json` | the same numbers, machine-readable (source of README/FACTS numbers) |
| `sdwan.json` | `noc-pulse sdwan --json results/sdwan.json` | full SD-WAN simulation result |
| `shift_handover.md` | `noc-pulse handover --hours 8 --markdown results/shift_handover.md` | NOC shift-handover note for the last 8 hours of the run |
| `sample_raw.csv` | `noc-pulse export-sample --hours 1 --out results/sample_raw.csv` | the last hour of raw samples, anonymised (target ids only, IPs in error text masked) |
| `pytest.txt` | `python -m pytest > results/pytest.txt` | test run used for the test count |
| `fault_demo/` | `noc-pulse demo` | fault-injection demo: timeline, tickets with worklogs and diagnostics, summary |

The raw SQLite database (`data/noc.db`) is not committed: it is large and contains nothing
that is not summarised here.
