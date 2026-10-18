from narrative.operations import Operations


def test_display_map_resolves_pending_references_and_formal_objects(tmp_path):
    app = Operations(tmp_path / "labels.sqlite3")
    source_id = app.import_text("第一章", "甲与乙送别。")
    run_id = app.create_run(source_id)
    evidence = app.locate(source_id, "甲与乙送别")[0].model_dump()
    objects = [
        {"kind": "entity", "id": "a", "name": "甲", "entity_type": "person", "evidence": [evidence]},
        {"kind": "entity", "id": "b", "name": "乙", "entity_type": "person", "evidence": [evidence]},
        {"kind": "event", "id": "e", "title": "甲与乙送别", "action": "narrative", "participants": [{"entity_id": "a", "role": "送行"}, {"entity_id": "b", "role": "离开"}], "evidence": [evidence]},
        {"kind": "relation", "id": "r", "subject_id": "a", "predicate": "送别", "object_id": "b", "evidence": [evidence]},
        {"kind": "storyline", "id": "s", "title": "二人离别", "line_type": "main", "event_ids": ["e"], "evidence": [evidence]},
        {"kind": "classification", "id": "c", "target_kind": "entity", "target_id": "a", "category": "人物特点/恋旧", "evidence": [evidence]},
    ]
    candidate_id = app.propose_analysis_objects(objects, app.project_info().revision)
    pending = app.analysis_display_map(candidate_id)
    labels = pending["items"]
    assert labels[source_id]["label"] == "第一章"
    assert labels[run_id]["label"].startswith("第一章 · 待提取")
    assert labels["a"]["label"] == "甲" and labels["a"]["layer"] == "candidate"
    assert labels["e"]["label"] == "甲与乙送别"
    assert "甲（送行）" in labels["e"]["summary"]
    assert labels["r"]["label"] == "甲 → 送别 → 乙"
    assert labels["s"]["label"] == "主线：二人离别"
    assert labels["c"]["label"] == "甲 · 人物特点/恋旧"
    assert "人物2" in labels[candidate_id]["label"]
    assert "甲与乙送别" not in app.analysis_display_map()["items"]
    assert "a" not in app.analysis_display_map()["items"]

    app.accept_candidate(candidate_id, 1)
    formal = app.analysis_display_map()["items"]
    assert formal["r"]["label"] == "甲 → 送别 → 乙"
    assert formal["r"]["layer"] == "formal"


def test_display_map_disambiguates_names_and_marks_missing_refs(tmp_path):
    app = Operations(tmp_path / "duplicates.sqlite3")
    source_id = app.import_text("同名", "甲和另一个甲见面。")
    evidence = app.locate(source_id, "甲和另一个甲")[0].model_dump()
    candidate = app.propose_analysis_objects([
        {"kind": "entity", "id": "first-id", "name": "甲", "evidence": [evidence]},
        {"kind": "entity", "id": "second-id", "name": "甲", "evidence": [evidence]},
        {"kind": "relation", "id": "unresolved", "subject_id": "first-id", "object_id": "unknown-person", "predicate": "见面", "evidence": [evidence]},
    ], app.project_info().revision)
    items = app.analysis_display_map(candidate)["items"]
    assert items["first-id"]["label"] == items["second-id"]["label"] == "甲"
    assert items["first-id"]["qualified_label"] != items["second-id"]["qualified_label"]
    assert items["unknown-person"]["kind"] == "missing"
    assert "未找到的人物" in items["unresolved"]["label"]
