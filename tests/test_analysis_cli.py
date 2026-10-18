import json

from narrative import cli
from narrative.operations import Operations


def test_analysis_cli_reads_through_operations(tmp_path, monkeypatch, capsys):
    project = tmp_path / "analysis.sqlite3"
    operations = Operations(project)
    source_id = operations.import_text("人物", "甲沉着地走进屋内。")
    evidence = operations.locate(source_id, "甲沉着地走进屋内。")[0]
    candidate_id = operations.propose_objects(
        [
            {"kind": "entity", "id": "person-a", "name": "甲", "entity_type": "person"},
            {
                "kind": "assertion", "id": "trait-a", "subject_id": "person-a",
                "predicate": "性格特点", "value": "沉着", "scope": "world",
                "properties": {"analysis_kind": "trait"},
                "evidence": [evidence.model_dump(mode="json")],
            },
        ]
    )
    operations.accept_candidate(candidate_id, operations.review_candidate(candidate_id).version)
    revision = operations.project_info().revision
    monkeypatch.setattr(
        "sys.argv", ["narrative", "--db", str(project), "character", "person-a"]
    )

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["entity"]["name"] == "甲"
    assert result["traits"][0]["value"] == "沉着"
    assert operations.project_info().revision == revision
