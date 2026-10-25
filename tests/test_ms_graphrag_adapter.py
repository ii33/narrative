"""Microsoft GraphRAG exports remain unreviewed search hints."""

import json

import pytest

from narrative.graph_operations import GraphOperations


def test_preview_checks_exact_quote_and_leaves_graph_unchanged(tmp_path):
    ops = GraphOperations(tmp_path / "project.sqlite3", create=True)
    source_id = ops.import_source("合成短章", "林舟把铜钥匙交给沈岚。次日沈岚打开蓝柜。")
    revision = ops.graph_project_info()["revision"]
    export_path = tmp_path / "export.json"
    export_path.write_text(
        json.dumps(
            {
                "schema": "microsoft-graphrag-standard-export/1",
                "method": "standard",
                "tables": {
                    "documents": [{"id": "d1", "title": "synthetic.txt"}],
                    "text_units": [{"id": "t1", "document_id": "d1"}],
                    "entities": [{"id": "e1", "title": "林舟"}],
                    "relationships": [],
                    "communities": [],
                    "community_reports": [],
                    "covariates": [
                        {"id": "c1", "text_unit_id": "t1", "source_text": "林舟把铜钥匙交给沈岚。", "status": "TRUE"},
                        {"id": "c2", "text_unit_id": "t1", "source_text": "林舟赠送了一把钥匙", "status": "TRUE"},
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    preview = ops.graphrag_preview(export_path, {"d1": source_id})

    assert preview["table_counts"]["entities"] == 1
    assert preview["claims"][0]["exact_quote_eligible"] is True
    assert preview["claims"][1]["exact_quote_eligible"] is False
    assert preview["formal_candidates_created"] == 0
    assert ops.graph_project_info()["revision"] == revision
    assert ops.graph_candidates() == []


def test_stage_sources_uses_fresh_root_without_changing_project(tmp_path):
    ops = GraphOperations(tmp_path / "project.sqlite3", create=True)
    source_id = ops.import_source("合成短章", "阿舟把铜钥匙交给沈岚。")
    revision = ops.graph_project_info()["revision"]
    root = tmp_path / "graphrag-run"

    manifest = ops.graphrag_stage_sources(root, [source_id])

    assert (root / "input" / f"{source_id}.txt").read_text(encoding="utf-8") == "阿舟把铜钥匙交给沈岚。"
    assert manifest["sources"][0]["source_id"] == source_id
    assert ops.graph_project_info()["revision"] == revision
    export_path = root / "export.json"
    export_path.write_text(json.dumps({
        "schema": "microsoft-graphrag-standard-export/1", "method": "standard",
        "tables": {
            "documents": [{"id": "d1", "title": f"{source_id}.txt"}],
            "text_units": [{"id": "t1", "document_id": "d1"}],
            "entities": [], "relationships": [], "communities": [], "community_reports": [],
            "covariates": [{"id": "c1", "text_unit_id": "t1", "source_text": "铜钥匙交给沈岚"}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    assert ops.graphrag_preview_staged(root, export_path)["claims"][0]["exact_quote_eligible"]
    with pytest.raises(FileExistsError):
        ops.graphrag_stage_sources(root, [source_id])
