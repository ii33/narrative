"""Only current project format is accepted; no conversion surface remains."""
import hashlib
import sqlite3

import pytest

from narrative.native_bridge import NativeBridge
from narrative.operations import Operations
from narrative.project_package import layout


@pytest.mark.parametrize("old_schema", [7, 8, 9])
def test_old_schema_is_rejected_without_writing_and_without_conversion_api(tmp_path, monkeypatch, old_schema):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "old.nproj"
    Operations.create_graph_project(entry)
    db = layout(entry).db
    with sqlite3.connect(db) as connection:
        connection.execute(f"PRAGMA user_version={old_schema}")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="不支持旧版"):
        Operations.open(entry)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not hasattr(Operations, "convert_project_copy")
    assert not hasattr(NativeBridge, "convertProjectCopy")
