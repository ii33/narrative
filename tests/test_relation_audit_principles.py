"""Shared counterpart is not evidence that relationship roles are synonyms."""
from narrative.assistant import _first_prompt
from narrative.harness import CONVERSATION_SYSTEM_PROMPT
from narrative.property_audit import audit_properties
from narrative.tool_api import TOOLS


def data():
    nodes = [{"id": "P1", "node_type": "person", "name": "甲"}, {"id": "P2", "node_type": "person", "name": "乙"}]
    nodes.append({"id": "E1", "node_type": "event", "name": "来往", "disclosure": {"source_id": "SRC1", "start": 0}})
    records = [{"kind": "property_definition", "id": name, "value_type": "node", "cardinality": "one"} for name in ["朋友", "同伴"]]
    records += [{"kind": "property_change", "id": f"PV{i}", "node_id": "P1", "property_id": name,
                 "value": "P2", "op": "set", "sequence": 0, "event_id": "E1", "scope": "world", "category": "关系",
                 "disclosure": {"source_id": "SRC1", "start": 0}, "evidence": {"source_id": "SRC1", "quote": "甲与乙来往。"}}
                for i, name in enumerate(["朋友", "同伴"], 1)]
    return nodes, records


def test_distinct_roles_for_same_counterpart_do_not_suggest_synonym_merge():
    nodes, records = data()
    issues = audit_properties(records, nodes)["issues"]
    assert not any(row["code"] in {"aspect_overlap", "duplicate_value", "name_variant"} for row in issues)
    assert not any("合并" in row["suggestion"] for row in issues)


def test_exact_duplicate_at_same_disclosure_is_still_reported():
    nodes, records = data()
    records.append(dict(records[-1], id="PV3"))
    duplicates = [row for row in audit_properties(records, nodes)["issues"] if row["code"] == "duplicate_value"]
    assert len(duplicates) == 1
    assert set(duplicates[0]["record_ids"]) == {"PV2", "PV3"}


def test_task_purpose_balances_relationship_coverage_and_correction():
    prompt = CONVERSATION_SYSTEM_PROMPT + "\n" + _first_prompt("复核人物关系")
    for principle in ["人物齐全不等于关系齐全", "核对属性和关系的建立、变化、终止及后续存续", "同一对对象可同时存在不同关系、身份或作用", "不能仅因指向同一对象就视为同义合并", "查漏、勘误"]:
        assert principle in prompt
    for principle in ["一次性人际作用也须核对", "不由共现或单次作用外推长期立场", "工具未成功的操作不得声称已完成"]:
        assert principle in prompt
    assert "相同主体或对象不是同义依据" in TOOLS["merge_properties"].summary

def test_default_relation_coverage_is_semantic_and_does_not_depend_on_plot_importance():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    extraction = DEFAULT_PROMPTS['extraction']['text']
    review = DEFAULT_PROMPTS['review']['text']
    assert all(category in extraction for category in ['身份、社会隶属与职务', '交往关系', '临时角色或作用'])
    assert '次要、无名人物也须核对，不按主线重要性筛除' in extraction
    assert '原文明示并影响叙事' not in extraction
    assert '沿人物和事件角色查漏，而不是只检查已经存在的关系' in review
    assert '不枚举所有人物配对' in extraction and '不为每人凑关系' in extraction
    assert '不将本次复核扩成无目的全库审计' in review


def test_temporary_roles_and_one_sided_claims_are_not_invented_persistent_mutual_relations():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    prompt = DEFAULT_PROMPTS['extraction']['text']
    assert '没有独立关系依据时，保留为事件事实或事件角色属性' in prompt
    assert '不臆造终止事件' in prompt
    assert '不由共现或单次作用外推长期立场' in prompt
    assert '只有单方所信、所称或自认而无客观成立依据' in prompt
    assert '对应holder的cognition或claim' in prompt and '不能替另一方补认知' in prompt
    assert '不按对白形式机械决定scope' in prompt  # supported dialogue facts still may be world facts
    assert 'symmetric表示关系性质，不是双方认可或知情的证据' in prompt
    assert '不能把单向关系原名倒填成双向' in prompt
    assert '同一对称事实不因两端都需展示就重复录入' in prompt


def test_relation_self_check_keeps_incremental_completion_and_optional_locator_contract():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    extraction = DEFAULT_PROMPTS['extraction']['text']
    review = DEFAULT_PROMPTS['review']['text']
    assert '随读取和整理本批事实' in extraction and '及时提交一批' in extraction
    assert '这是本批语义查漏，不是提交后重跑全库审计' in extraction
    assert '直接submit_extraction' in extraction and '程序负责逐字匹配' in extraction
    assert '不为可能出错而预防性反复查验全部引文' in extraction
    assert 'accepted且无error' in extraction and 'info与建议不是必须清零的错误' in extraction
    assert '在有证据的终止事件追加remove指定对象或clear清空' in review and '后续存续证据' in review
    assert '不把录入错误伪装为故事失效' in review


def test_negative_polarity_is_not_hidden_in_positive_claim_edges():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    extraction = DEFAULT_PROMPTS['extraction']['text']
    assert '否定关系不能存成同一条正向关系' in extraction
    assert '即使scope是claim或cognition' in extraction
    assert '再靠引文或备注解释' in extraction
    assert '事件的言说内容等文字属性保留否定表达及说话者' in extraction
    assert '单方否认不直接证明world关系终止' in extraction


def test_transient_action_rule_is_explicit_without_fabricating_expiry():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    extraction = DEFAULT_PROMPTS['extraction']['text']
    assert '一次行为不等于持续关系' in extraction
    assert '禁止把person到person的作用外推为持续状态' in extraction
    assert '用该事件的施事、受事等角色属性保留事实' in extraction
    assert '不能为此编造结束时间' in extraction


def test_cardinality_means_collection_not_number_of_scalar_relationship_counterparts():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    extraction = DEFAULT_PROMPTS['extraction']['text']
    assert '人物标量关系按对方对象分别维护事件变更序列' in extraction
    assert '同一主体可并存多条one关系' in extraction
    assert '属性本身表达集合状态时才用many，value须为数组且每个元素符合value_type' in extraction
    assert '不能仅因存在多个关系对象就要求many' in extraction
    assert '不为消除错误盲改many' in extraction
    assert '在授权范围内经apply_changes显式修订' in extraction
    review = DEFAULT_PROMPTS['review']['text']
    assert 'many只用于数组集合状态' in review
    assert '不为此重做定位预检循环' in review
