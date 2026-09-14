"""Two runs compared: the same numbers in a different order are not a difference."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audit.scoring.variance import compare_runs


def _export(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run": {"job_id": "j", "code_version": "c"},
        "subject": {"as_of_date": "2026-09-11"},
        "calculations": rows,
        "steps": [],
        "sections": [],
        "plan": {},
    }


def _row(name: str, value: str, *, period: str | None = None, unit: str = "USD") -> dict[str, Any]:
    return {
        "name": name,
        "formula": f"{name} = a + b",
        "code_version": "c",
        "output_value": value,
        "output_unit": unit,
        "period": period,
        "parameters": {},
    }


def _written(tmp_path: Path, label: str, rows: list[dict[str, Any]]) -> Path:
    directory = tmp_path / label
    directory.mkdir()
    (directory / "run-export.json").write_text(json.dumps(_export(rows)), encoding="utf-8")
    return directory


class TestOrderInsideAKeyIsNotADifference:
    """`days_outstanding` records receivable, inventory and payable days under one key and
    one period, and the two MSFT runs emitted the same three numbers in a different order.
    Comparing position by position called all three different — 642 rows of "variance"
    that were nothing of the kind — so the comparison is of what came out, not of where.
    """

    def test_a_reordered_group_is_identical(self, tmp_path: Path) -> None:
        first = _written(
            tmp_path,
            "first",
            [
                _row("days_outstanding", "21.8", period="FY2022", unit="day"),
                _row("days_outstanding", "110.7", period="FY2022", unit="day"),
            ],
        )
        second = _written(
            tmp_path,
            "second",
            [
                _row("days_outstanding", "110.7", period="FY2022", unit="day"),
                _row("days_outstanding", "21.8", period="FY2022", unit="day"),
            ],
        )

        result = compare_runs(first, second)["calculations"]

        assert result["rows_differing"] == 0
        assert result["differing"] == []

    def test_a_changed_value_inside_a_group_is_still_caught(self, tmp_path: Path) -> None:
        first = _written(
            tmp_path,
            "first",
            [
                _row("present_value", "100"),
                _row("present_value", "200"),
            ],
        )
        second = _written(
            tmp_path,
            "second",
            [
                _row("present_value", "200"),
                _row("present_value", "201"),
            ],
        )

        result = compare_runs(first, second)["calculations"]

        assert result["rows_differing"] == 1
        assert result["differing"][0]["examples"] == [{"first": "100", "second": "201"}]

    def test_every_row_is_counted_not_just_every_key(self, tmp_path: Path) -> None:
        rows = [_row("present_value", str(n)) for n in range(5)]
        first = _written(tmp_path, "first", rows)
        second = _written(tmp_path, "second", rows)

        result = compare_runs(first, second)["calculations"]

        assert result["rows"] == (5, 5)
        assert result["rows_compared"] == 5
        assert result["first"] == 1
