"""FAT/SAT test authoring and running for Logix projects and BMS telemetry.

A suite is a set of cases; each case optionally sets tags, then asserts expected values/states. The
assertion operators and the report formatter live here (pure); the SDK-backed runner lives in the
server and reads/writes tags through an open project. The result is a pass/fail report suitable for a
factory- or site-acceptance record.

Suite shape (JSON):
  {"name": "CDU01 FAT",
   "cases": [
     {"name": "Pump interlock",
      "setup":  [{"tag": "Pump1_Run", "type": "BOOL", "value": 1}],
      "checks": [{"tag": "Pump1_Run", "type": "BOOL", "op": "eq", "value": 1},
                 {"tag": "CHWS_Temp", "type": "REAL", "op": "between", "value": [40, 55]}]}]}
For telemetry suites, checks use {"point": "..", "stat": "min|max|last", "op": "..", "value": ..}.
"""
from __future__ import annotations


def _between(a, e):
    lo, hi = e
    return lo <= a <= hi


OPS = {
    "eq": lambda a, e: a == e,
    "ne": lambda a, e: a != e,
    "gt": lambda a, e: a > e,
    "ge": lambda a, e: a >= e,
    "lt": lambda a, e: a < e,
    "le": lambda a, e: a <= e,
    "between": _between,
    "truthy": lambda a, e: bool(a),
    "falsy": lambda a, e: not bool(a),
}


def evaluate(op: str, actual, expected=None) -> bool:
    fn = OPS.get(op)
    if fn is None:
        raise ValueError(f"Unknown op: {op}. Supported: {sorted(OPS)}")
    try:
        return bool(fn(actual, expected))
    except TypeError:
        return False


def report_markdown(suite_name: str, cases: list) -> str:
    total_checks = sum(len(c["checks"]) for c in cases)
    passed_checks = sum(sum(1 for chk in c["checks"] if chk["passed"]) for c in cases)
    passed_cases = sum(1 for c in cases if c["passed"])
    lines = [f"# FAT/SAT Report: {suite_name}", "",
             f"**Cases:** {passed_cases}/{len(cases)} passed  |  "
             f"**Checks:** {passed_checks}/{total_checks} passed", ""]
    for c in cases:
        mark = "PASS" if c["passed"] else "FAIL"
        lines.append(f"## [{mark}] {c['name']}")
        for chk in c["checks"]:
            m = "ok" if chk["passed"] else "XX"
            val = chk.get("value", "")
            lines.append(f"- [{m}] {chk['tag']} {chk['op']} {val} -> actual: {chk['actual']}")
        lines.append("")
    return "\n".join(lines)
