from test_screenplay_ui import create_fixture


def seed(ops):
    sid = create_fixture(ops)
    segment = ops.read_screenplay(sid)["segments"][0]
    for title, texts in [("街道", ["安德烈敲窗三下。车窗降下。", "车里传出录音。"]), ("车内", ["驾驶者抬头。录音仍在播放。"]), ("街道再现", ["安德烈看清金属头盔。"])]:
        prior_ids = {u["id"] for u in segment["units"]}
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"title": title, "summary": title + "的声画变化。"}}, "after_id": segment["units"][-1]["id"] if segment["units"] else None}], segment["outline_revision"], reason="分场剧本样本")
        unit = next(u for u in segment["units"] if u["id"] not in prior_ids)
        after = None
        for text in texts:
            unit = ops.apply_script_block_changes(unit["id"], [{"op": "insert", "block": {"type": "action", "payload": {"text": text}}, "after_id": after}], [], unit["body_revision"], reason="分场正文")
            after = unit["blocks"][-1]["id"]
    return sid, ops.read_script_segment(segment["id"])
