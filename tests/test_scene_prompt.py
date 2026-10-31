"""The model must receive the composite-event meaning of a scene."""

from narrative.extraction import PROMPT_VERSION, build_prompt


def test_prompt_distinguishes_composite_events_from_locations() -> None:
    prompt = build_prompt("测试章节", "甲从大厅追到街上，乙投降。")
    assert PROMPT_VERSION == "extract/22"
    assert "由若干小事件组成的**复合事件**" in prompt
    assert "地点另建 location 节点" in prompt
    assert "换地点或时间跳跃可以提示边界，但不自动产生新场景" in prompt
    assert "不能只写地点名或「地点＋日期」" in prompt
    assert "「酒店房间」是 location" in prompt
    assert "「林舟撞破私情并与陈默冲突」是 scene" in prompt
    assert "地点变了、时间跳了，就是新场景" not in prompt
    assert "场景按地点/回合切" not in prompt
    assert "大事件 → 场景 → 事件 → 动作" in prompt
    assert "甲从大厅追到街上，乙投降。" in prompt


def test_graph_context_does_not_teach_location_style_scene_names() -> None:
    from narrative.extraction import graph_context

    class ExistingGraph:
        def graph_snapshot(self):
            return {"nodes": [{"id": "S1", "node_type": "scene", "name": "大厅"}]}

        def property_records(self):
            return []

    context = graph_context(ExistingGraph())
    assert "大厅" in context
    assert "仅在延续同一个既有复合事件时复用" in context
    assert "新场景按发生过程命名，不照抄地点式命名" in context
    assert "名字照这个写法" not in context
