---
name: rockwell-fat-sat
description: >-
  Author and run FAT/SAT acceptance tests for Rockwell/ControlLogix work with the rockwell_local MCP —
  assert expected tag values and states against an offline project, simulated telemetry, or a live
  controller, and emit a pass/fail report. Use this whenever the user wants to write, run or automate
  factory or site acceptance tests, a commissioning checklist, interlock/alarm verification, I/O
  checkout, or a point-to-point test for a Logix project — even before hardware exists. Reach for it
  whenever "FAT", "SAT", "acceptance test", "checkout", or "verify the logic" comes up.
---

# FAT/SAT testing for Logix projects

Acceptance testing should start long before hardware arrives and run the same way at the factory and
on site. So tests here are data-driven suites that assert expected tag values/states, and they run
against three sources with the **same suite format**: an offline project (software FAT, no hardware),
simulated telemetry (behavioral checks), and a live controller (SAT). Write the suite once; run it
wherever you are in the lifecycle.

## Suite format
```json
{"name": "CDU01 FAT",
 "cases": [
   {"name": "Pump interlock",
    "setup":  [{"tag": "Pump1_Run", "type": "BOOL", "value": 1}],
    "checks": [{"tag": "Pump1_Cmd", "type": "BOOL", "op": "eq", "value": 1},
               {"tag": "CHWS_Temp", "type": "REAL", "op": "between", "value": [40, 55]}]}]}
```
- `setup` (optional): tags to write before the checks (drive an input, then verify the response).
- `checks`: assertions. `op` is one of `eq, ne, gt, ge, lt, le, between, truthy, falsy`
  (`between` takes `[lo, hi]`).
- `type` is the Logix atomic type (BOOL, DINT, REAL, …). Address tags by **bare name** (resolved to
  controller scope) or pass `program` for a program-scoped tag — do NOT hand the SDK a bare name as a
  raw path, it needs an XPath and the tools build it for you.

## Tools (rockwell_local)
- `run_fat_suite(project_file_path, suite, mode)` — run a suite against an **open project's** tags.
  `mode=OFFLINE` reads/writes the project's stored values (software FAT, no hardware); `mode=ONLINE`
  runs against the live controller (SAT — requires an online session, see `rockwell-build-commission`).
  Returns per-check actual-vs-expected results, case pass/fail, and a `report_markdown`.
- `run_fat_suite_telemetry(points, suite, samples)` — run checks against **simulated** telemetry
  (no hardware). Checks use `{point, stat: min|max|last, op, value}` — great for proving alarm ranges
  and behavior before a controller exists.
- `controller_health(project_file_path)` — connection state (works offline) and, online, the key
  mode; a quick pre/post-download read for SAT.

## Workflow
1. **Software FAT (no hardware):** build/generate the project, `open_project`, write the suite, run
   `run_fat_suite(..., mode="OFFLINE")`. Drive inputs in `setup`, assert outputs in `checks`. Save the
   `report_markdown` as the FAT record.
2. **Behavioral checks:** `run_fat_suite_telemetry` to confirm simulated points stay in range / alarms
   trip, before the logic meets real I/O.
3. **SAT (on hardware):** go online (confirm-gated, see `rockwell-build-commission`), then the SAME
   suite with `mode="ONLINE"`, plus `controller_health` before and after.

## Good tests
- Assert the *response*, not just the input you set: set `Pump1_Run`, then check `Pump1_Cmd` and any
  interlock/permissive it drives.
- Cover alarm/interlock edges with `between`, `ge`, `le` — the failures are where safety lives.
- Name cases after the requirement they prove; the report is the acceptance evidence a human signs.

## Honesty
OFFLINE and telemetry suites need no hardware and really execute. An ONLINE (SAT) run needs a live
(or emulated) controller and an activation; if neither is present, run the suite OFFLINE and say the
result is a software FAT, not a controller SAT — don't imply hardware was tested.
