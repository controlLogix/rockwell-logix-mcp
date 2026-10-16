import pytest

import fatsat


@pytest.mark.parametrize("op,a,e,expected", [
    ("eq", 1, 1, True), ("eq", 1, 2, False),
    ("ne", 1, 2, True), ("ne", 2, 2, False),
    ("gt", 3, 2, True), ("gt", 2, 2, False),
    ("ge", 2, 2, True), ("ge", 1, 2, False),
    ("lt", 1, 2, True), ("lt", 2, 2, False),
    ("le", 2, 2, True), ("le", 3, 2, False),
    ("between", 48, [40, 55], True),
    ("between", 40, [40, 55], True),   # inclusive low
    ("between", 55, [40, 55], True),   # inclusive high
    ("between", 39.9, [40, 55], False),
    ("truthy", 1, None, True), ("truthy", 0, None, False),
    ("falsy", 0, None, True), ("falsy", 1, None, False),
])
def test_evaluate_ops(op, a, e, expected):
    assert fatsat.evaluate(op, a, e) is expected


def test_evaluate_unknown_op_raises():
    with pytest.raises(ValueError):
        fatsat.evaluate("approx", 1, 1)


def test_evaluate_type_mismatch_is_false_not_error():
    # comparing incompatible types must fail the check, not raise
    assert fatsat.evaluate("gt", "a", 1) is False


def test_report_markdown_counts_and_marks():
    cases = [
        {"name": "ok case", "passed": True, "checks": [
            {"tag": "A", "op": "eq", "value": 1, "actual": 1, "passed": True}]},
        {"name": "bad case", "passed": False, "checks": [
            {"tag": "B", "op": "between", "value": [40, 55], "actual": 99, "passed": False}]},
    ]
    md = fatsat.report_markdown("CDU FAT", cases)
    assert "# FAT/SAT Report: CDU FAT" in md
    assert "Cases:** 1/2 passed" in md
    assert "Checks:** 1/2 passed" in md
    assert "[PASS] ok case" in md
    assert "[FAIL] bad case" in md
    assert "B between [40, 55] -> actual: 99" in md
