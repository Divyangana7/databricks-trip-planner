"""
Pytest suite for data quality — runs the same checks as quality/checks.py but as
standard tests, so `pytest` gives a green/red gate in CI or locally.

Run (from repo root, with LAKEBASE_URL set or Databricks secret configured):
    pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality import checks  # noqa: E402


@pytest.mark.parametrize("check_fn", checks.CHECKS, ids=[c.__name__ for c in checks.CHECKS])
def test_quality_check(check_fn):
    result = check_fn()
    # Warn-severity checks are allowed to fail without failing the suite.
    if result["severity"] == "warn" and not result["passed"]:
        pytest.skip(f"warn-only: {result['detail']}")
    assert result["passed"], result["detail"]


def test_overall_gate():
    report = checks.run_all()
    assert report["passed"], (
        f"{report['n_failed_errors']} error-severity checks failed: "
        f"{[r['name'] for r in report['results'] if r['severity']=='error' and not r['passed']]}"
    )
