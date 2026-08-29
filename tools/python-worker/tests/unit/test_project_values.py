from __future__ import annotations

import json
from pathlib import Path

from impeller_reliability.persistence.project_values import require_canonical_project_id


def test_accepts_shared_canonical_non_v4_project_id_fixture() -> None:
    fixture_path = Path(__file__).parents[4] / "fixtures" / "contracts" / "canonical-project-id-non-v4.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["expectedVersion"] == 7
    assert require_canonical_project_id(fixture["projectId"]) == fixture["projectId"]
