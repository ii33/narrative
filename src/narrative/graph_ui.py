"""Streamlit front end for new, isolated schema-v4 graph projects."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

from .operations import OperationError, Operations

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["分析检索", "提取", "提取成果", "资产库", "世界观", "实体表", "时间轴", "原文证据"]
TYPE_LABELS = {
    "person": "人物", "item": "物品", "location": "地点", "event": "事件",
    "scene": "场景", "storyline": "故事线", "group": "群体", "other": "其他",
}
SCOPE_LABELS = {"world": "世界事实", "claim": "人物主张", "knowledge": "人物认知"}
ASSESSMENT_LABELS = {
    "supported": "有支持证据", "disputed": "有争议", "retracted": "已撤回", "unresolved": "尚未判定",
}


def _key(*parts: str) -> str:
    return "graph:" + ":".join(parts)


def _project_dir() -> Path:
    return Path(os.environ.get("NARRATIVE_PROJECT_DIR", ROOT / "data")).resolve()


def _obj_label(obj: dict | None, by_id: dict[str, dict] | None = None) -> str:
    if not obj:
        return "未找到引用对象"
    if obj.get("kind") == "node":
        return f"{TYPE_LABELS.get(str(obj.get('node_type')), '节点')} · {obj.get('name', '未命名')}"
    if obj.get("kind") == "predicate":
        return obj.get("label", obj.get("id", "谓词"))
    if obj.get("kind") == "source_order":
        return "原文披露顺序"
    return str(obj.get("label") or obj.get("name") or obj.get("value") or obj.get("kind") or "对象")


def _node_label(node: dict, nodes: list[dict] | None = None) -> str:
    label = _obj_label(node)
    if nodes and sum(1 for other in nodes if other.get("node_type") == node.get("node_type") and other.get("name") == node.get("name")) > 1:
        label += f" · {str(node.get('id', ''))[:6]}"
    return label


def _ref(value: str | None, by_id: dict[str, dict]) -> str:
    return _obj_label(by_id.get(value), by_id) if value else "未指定"


def _assertion_label(assertion: dict, by_id: dict[str, dict]) -> str:
    subject = _ref(assertion.get("subject_id"), by_id)
    predicate_id = assertion.get("predicate_id")
    predicate_obj = by_id.get(predicate_id) if isinstance(predicate_id, str) else None
    predicate = _ref(assertion.get("predicate_id"), by_id)
    target = _ref(assertion.get("object_id"), by_id) if assertion.get("object_id") else str(assertion.get("value", "未指定"))
    role = assertion.get("qualifiers", {}).get("role")
    if predicate_obj and predicate_obj.get("semantic") == "temporal":
        left = _time_ref(assertion.get("temporal_left"), by_id)
        right = _time_ref(assertion.get("temporal_right"), by_id)
        if predicate_obj.get("id") == "before":
            relation = f"{left} 先于 {right}"
        elif predicate_obj.get("id") == "after":
            relation = f"{left} 后于 {right}"
        else:
            relation = f"{left} —{predicate}→ {right}"
    else:
        relation = f"{subject} — {predicate} → {target}"
    if role:
        relation += f"（角色：{role}）"
    return relation


def _evidence_text(evidence: dict, sources: dict[str, dict]) -> str:
    source_id = evidence.get("source_id")
    source = sources.get(source_id, {}) if isinstance(source_id, str) else {}
    title = source.get("title", "来源未知")
    start, end = evidence.get("start"), evidence.get("end")
    quote = evidence.get("quote", "")
    return f"{title} · 字符 {start}–{end}：{quote}"


def _node_selector(nodes: list[dict], label: str, key: str, default_id: str | None = None) -> str | None:
    if not nodes:
        st.info("还没有可浏览节点。导入原文后，可在「提取」启动分析并审核候选。")
        return None
    ids = [row["id"] for row in nodes]
    selected = default_id if default_id in ids else ids[0]
    return st.selectbox(label, ids, index=ids.index(selected), format_func=lambda identity: _node_label(next((row for row in nodes if row["id"] == identity), {}), nodes), key=key)


def _navigate_node(node_id: str) -> None:
    st.session_state["graph:selected_node"] = node_id
    st.session_state["graph:pending-node"] = node_id
    st.session_state["graph:pending-page"] = "实体表"


def _render_node_detail(node_id: str, snapshot: dict, sources_by_id: dict[str, dict]) -> None:
    objects = {row["id"]: row for row in snapshot.get("objects", [])}
    node = objects.get(node_id)
    if not node:
        st.warning("选中的节点已不在当前修订中。")
        return
    st.subheader(node.get("name", "未命名节点"))
    st.caption(f"{TYPE_LABELS.get(node.get('node_type'), '节点')} · {node.get('subtype') or '未细分'} · {node.get('identity_note') or '暂无身份说明'}")
    with st.expander("别名与节点属性", expanded=True):
        aliases = node.get("aliases") or []
        st.write("别名：" + ("、".join(aliases) if aliases else "无"))
        occurrence = node.get("occurrence")
        if occurrence:
            st.write({"事件发生区间": occurrence})
    with st.expander("技术详情"):
        st.caption(f"节点 ID：{node_id}")

    predicates = {row["id"]: row for row in snapshot.get("predicates", [])}
    evidences = {row["id"]: row for row in snapshot.get("evidences", [])}
    evidence_links: dict[str, list[dict]] = {}
    for link in snapshot.get("evidence_links", []):
        evidence_links.setdefault(link["assertion_id"], []).append(link)
    assertions = [
        row for row in snapshot.get("assertions", [])
        if node_id in {row.get("subject_id"), row.get("object_id"), row.get("holder_id")}
    ]
    if not assertions:
        st.info("此节点尚无已审核断言；待审候选不会混入正式图。")
        return
    st.markdown("#### 事实、主张、状态与关联")
    for assertion in assertions:
        predicate = predicates.get(assertion.get("predicate_id"), {})
        semantic = predicate.get("semantic", "attribute")
        icon = {"relation": "关系", "participation": "事件参与", "temporal": "时间关系", "attribute": "属性"}.get(semantic, semantic)
        title = f"{icon} · {_assertion_label(assertion, objects)}"
        with st.expander(title):
            st.write(f"范围：{SCOPE_LABELS.get(assertion.get('scope'), assertion.get('scope'))}；判定：{ASSESSMENT_LABELS.get(assertion.get('assessment'), assertion.get('assessment'))}；来源性质：{assertion.get('origin', 'original')}")
            if assertion.get("holder_id"):
                st.write("主张／认知持有人：" + _ref(assertion["holder_id"], objects))
            st.write(f"语气：{assertion.get('modality', 'actual')}；确定性：{assertion.get('certainty', 'unknown')}；极性：{assertion.get('polarity', 'positive')}")
            validity = assertion.get("validity", {})
            st.write("有效时间：" + _time_extent(validity, objects))
            for link in evidence_links.get(assertion["id"], []):
                evidence = evidences.get(link["evidence_id"])
                if evidence:
                    st.markdown(f"证据（{link.get('role', 'supports')}）：{_evidence_text(evidence, sources_by_id)}")
                    if st.button("打开来源段落", key=f"source-from-evidence:{node_id}:{assertion['id']}:{evidence['id']}"):
                        st.session_state["graph:pending-page"] = "原文证据"
                        st.session_state["graph:pending-source"] = evidence.get("source_id")
                        st.rerun()
            related = [assertion.get("subject_id"), assertion.get("object_id")]
            for related_id in related:
                if related_id and related_id != node_id and objects.get(related_id, {}).get("kind") == "node":
                    if st.button("打开：" + _obj_label(objects[related_id]), key=f"open-node:{node_id}:{assertion['id']}:{related_id}"):
                        _navigate_node(related_id)
                        st.rerun()
            if assertion.get("derived_from"):
                derived = [objects.get(identity, {}) for identity in assertion["derived_from"]]
                st.caption("依据：" + "；".join(_assertion_label(row, objects) for row in derived))
                with st.expander("技术详情 · 依据断言 ID"):
                    st.code("\n".join(assertion["derived_from"]))


def _time_ref(ref: dict | None, by_id: dict[str, dict] | None = None) -> str:
    if not ref:
        return "时间未知"
    if ref.get("form") == "number":
        return f"{ref.get('number')} {ref.get('timeline_id', 'story')}"
    if ref.get("form") == "datetime":
        return str(ref.get("datetime"))
    boundary = {"start": "开始", "end": "结束"}.get(str(ref.get("boundary")), "边界")
    event_id = ref.get("event_id")
    event = (by_id or {}).get(event_id, {}) if isinstance(event_id, str) else {}
    return f"{event.get('name', '未找到的事件')}的{boundary}"


def _time_bound(bound: dict | None, by_id: dict[str, dict] | None = None) -> str:
    if not bound:
        return "未知"
    state = bound.get("state", "unknown")
    if state == "unknown":
        return "未知（未提供时间）"
    if state == "unbounded":
        return "无界"
    return _time_ref(bound.get("ref"), by_id)


def _time_extent(extent: dict | None, by_id: dict[str, dict] | None = None) -> str:
    if not extent:
        return "未知"
    return f"{_time_bound(extent.get('start'), by_id)} → {_time_bound(extent.get('end'), by_id)}"


def _graphviz(query: dict, type_filter: list[str]) -> str:
    nodes = [node for node in query.get("nodes", []) if node.get("node_type") in type_filter]
    ids = {node["id"] for node in nodes}
    lines = ["digraph narrative {", 'graph [rankdir="LR", bgcolor="transparent"];', 'node [shape="box", style="rounded,filled", fillcolor="#eef4ff", color="#7891b5", fontname="Microsoft YaHei"];', 'edge [fontname="Microsoft YaHei", color="#667085"];']
    for node in nodes:
        name = node.get("name", "未命名")
        if sum(1 for other in nodes if other.get("node_type") == node.get("node_type") and other.get("name") == name) > 1:
            name += f" · {str(node.get('id', ''))[:6]}"
        label = f"{TYPE_LABELS.get(node.get('node_type'), '节点')}\n{name}"
        lines.append(f'"{node["id"]}" [label={json.dumps(label, ensure_ascii=False)}];')
    for edge in query.get("edges", []):
        subject, target = edge.get("subject_id"), edge.get("object_id")
        if subject not in ids or target not in ids:
            continue
        label = edge.get("label", "关联")
        if edge.get("role"):
            label += f" · {edge['role']}"
        if edge.get("scope") != "world":
            label += f" · {SCOPE_LABELS.get(edge.get('scope'), edge.get('scope'))}"
        if edge.get("origin") == "inference":
            label += " · 推断"
        lines.append(f'"{subject}" -> "{target}" [label={json.dumps(label, ensure_ascii=False)}];')
    lines.append("}")
    return "\n".join(lines)


def _hierarchy_lines(view: dict, *, max_rows: int = 120, shared: bool = False) -> list[str]:
    """Render only explicit containment paths; shared people remain separate roots."""
    by_id = {node["id"]: node for node in view.get("nodes", [])}
    primary_types = {"storyline", "scene", "event"}
    roots = [identity for identity in view.get("roots", [])
             if (by_id.get(identity, {}).get("node_type") not in primary_types) == shared]
    priority = {"storyline": 0, "scene": 1, "event": 2}
    roots.sort(key=lambda identity: (priority.get(by_id.get(identity, {}).get("node_type"), 9),
                                    by_id.get(identity, {}).get("name", ""), identity))
    lines: list[str] = []
    seen: set[str] = set()

    def visit(identity: str, depth: int) -> None:
        if identity in seen or identity not in by_id or len(lines) >= max_rows:
            return
        seen.add(identity)
        node = by_id[identity]
        indent = "　" * min(depth, 12)
        lines.append(f"{indent}{'↳ ' if depth else ''}{TYPE_LABELS.get(node.get('node_type'), '节点')} · {node.get('name', '未命名')}")
        for child in node.get("child_ids", []):
            visit(child, depth + 1)

    for root in roots:
        visit(root, 0)
    if len(lines) >= max_rows or view.get("truncated"):
        lines.append("…列表已截取，请缩小范围查看。")
    return lines


def _home() -> None:
    project_dir = _project_dir()
    st.title("叙事图工作台")
    st.caption("从最近工程进入，或创建独立的 v4 图项目。旧版工程保持原样，本工作台不会迁移或覆盖它们。")
    projects = Operations.list_projects(project_dir)
    if projects:
        st.subheader("最近工程")
        for project in projects:
            col, detail = st.columns([2, 3])
            with col:
                st.write(project.get("title") or project["name"])
                changed = datetime.fromtimestamp(project.get("modified_at", 0)).strftime("%Y-%m-%d %H:%M")
                st.caption(f"{project['name']} · 最近修改 {changed}")
            with detail:
                if project.get("status") == "ready" and project.get("version") == 4:
                    st.caption(f"v4 · 修订 {project.get('revision', 0)}")
                    if st.button("打开工程", key=f"open:{project['name']}"):
                        st.session_state["graph:path"] = project["path"]
                        st.session_state["graph:page"] = "分析检索"
                        st.rerun()
                else:
                    version = project.get("version")
                    reason = f"版本 {version}" if version is not None else "无法识别"
                    st.warning(f"旧版工程（{reason}），当前 v4 工作台不打开；可新建工程继续使用。")
    else:
        st.info("还没有本工作台可打开的工程。新建后先导入原文。")

    st.subheader("新建工程")
    with st.form("graph:new-project"):
        title = st.text_input("作品名称", placeholder="例如：我的小说")
        slug = st.text_input("工程代号", placeholder="例如：my-novel")
        create = st.form_submit_button("创建 v4 工程")
    if create:
        safe_slug = slug.strip()
        if not re.fullmatch(r"[\w-]{1,64}", safe_slug, flags=re.UNICODE):
            st.error("工程代号限 1–64 个字母、数字、中文、下划线或短横线。")
        elif not title.strip():
            st.error("请填写作品名称。")
        else:
            path = project_dir / f"{safe_slug}.sqlite3"
            try:
                ops = Operations.create_graph_project(path)
                ops.set_graph_title(title.strip())
                st.session_state["graph:path"] = str(path)
                st.session_state["graph:page"] = "分析检索"
                st.rerun()
            except (OperationError, ValueError, OSError) as exc:
                st.error(str(exc))


def _source_import(ops) -> None:
    with st.expander("导入章节原文", expanded=not bool(ops.sources())):
        with st.form("graph:source-import"):
            title = st.text_input("章节标题", key="graph:source-title")
            body = st.text_area("原文正文", height=220, key="graph:source-body")
            submitted = st.form_submit_button("保存原文")
        if submitted:
            try:
                source_id = ops.import_source(title, body)
                st.success("原文已保存。分析会读取这份不可变来源；后续改文请另存为新章节。")
                st.session_state["graph:last-source"] = source_id
                st.rerun()
            except (OperationError, ValueError) as exc:
                st.error(str(exc))


def _analysis_page(ops, snapshot: dict, sources: list[dict]) -> None:
    st.header("分析检索")
    nodes = snapshot.get("nodes", [])
    types = sorted({node.get("node_type", "other") for node in nodes})
    source_by_id = {row["id"]: row for row in sources}
    with st.form("graph:search"):
        query_text = st.text_input("查找名称、别名或断言值", key="graph:search-text")
        default_types = types or list(TYPE_LABELS)
        selected_types = st.multiselect("显示节点类型", list(TYPE_LABELS), default=default_types, format_func=lambda value: TYPE_LABELS[value])
        context_options = [row for row in snapshot.get("contexts", []) if row.get("kind") == "context"]
        context_ids = [row["id"] for row in context_options] or ["canon"]
        context_id = st.selectbox("语境 / 视角", context_ids, format_func=lambda value: next((row["label"] for row in context_options if row["id"] == value), value))
        with st.expander("时间与披露范围（未知项会单独说明）"):
            time_on = st.checkbox("限定故事时间点")
            time_point = st.number_input("故事时间数值", value=0.0)
            disclosure_on = st.checkbox("限定文本披露位置")
            source_ids = [row["id"] for row in sources]
            disclosure_source = st.selectbox("披露来源", source_ids, format_func=lambda value: source_by_id.get(value, {}).get("title", value)) if source_ids else None
            disclosure_start = st.number_input("披露字符位置", min_value=0, value=0, step=1)
        submit = st.form_submit_button("检索图")
    if submit or "graph:last-query" not in st.session_state:
        st.session_state["graph:last-query"] = {
            "query": query_text,
            "context_id": context_id,
            "story_cut": {"form": "number", "number": time_point, "timeline_id": "story"} if time_on else None,
            "disclosure_cut": {"source_id": disclosure_source, "start": int(disclosure_start)} if disclosure_on and disclosure_source else None,
            "types": selected_types,
        }
    params = st.session_state.get("graph:last-query", {})
    try:
        result = ops.graph_query(query=params.get("query", ""), context_id=params.get("context_id", "canon"), story_cut=params.get("story_cut"), disclosure_cut=params.get("disclosure_cut"))
    except (OperationError, ValueError) as exc:
        st.error(str(exc))
        return
    allowed = list(params.get("types", list(TYPE_LABELS)))
    visible_nodes = [node for node in result.get("nodes", []) if node.get("node_type") in allowed]
    visible_ids = {node["id"] for node in visible_nodes}
    graph_view = dict(result)
    graph_view["nodes"] = visible_nodes
    graph_view["edges"] = [edge for edge in result.get("edges", []) if edge.get("subject_id") in visible_ids and edge.get("object_id") in visible_ids]
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"**修订 {result.get('revision', 0)} · {len(visible_nodes)} 个节点 · {len(graph_view['edges'])} 条直接断言边**")
        hierarchy = ops.graph_hierarchy(context_id=params.get("context_id", "canon"), story_cut=params.get("story_cut"), disclosure_cut=params.get("disclosure_cut"))
        with st.expander("事件层级", expanded=bool(hierarchy.get("edges"))):
            if not hierarchy.get("edges"):
                st.caption("尚无直接包含关系；节点未分组，关系图仍可使用。")
            for line in _hierarchy_lines(hierarchy):
                st.text(line)
            shared_lines = _hierarchy_lines(hierarchy, shared=True)
            if shared_lines:
                st.caption(f"人物、地点与物件（{len(shared_lines)}）")
                for line in shared_lines:
                    st.text(line)
        st.caption("图中连线只表示已审核的直接断言；未展示的间接联系不会被画成事实。")
        if visible_nodes:
            st.graphviz_chart(_graphviz(graph_view, allowed), width="stretch")
        else:
            st.info("没有符合筛选的节点。可以清空关键词、扩大类型或检查时点。")
        if result.get("uncertainties"):
            st.warning("以下断言因时间或披露顺序未知，没有按已知事实展示。")
            for item in result["uncertainties"]:
                st.write(item.get("reason", "时间信息未知"))
    with right:
        # Never use the unrestricted snapshot to populate a disclosure-cut search selector.
        available = [node for node in visible_nodes if node.get("node_type") in allowed]
        selected = _node_selector(available, "查看节点详情并继续导航", "graph:analysis-node", st.session_state.get("graph:selected_node"))
        if selected:
            st.session_state["graph:selected_node"] = selected
            st.markdown(f"**{_obj_label(next((row for row in nodes if row['id'] == selected), None))}**")
            connected = [edge for edge in result.get("edges", []) if selected in {edge.get("subject_id"), edge.get("object_id")}]
            st.write(f"当前检索下有 {len(connected)} 条直接连接。")
            if st.button("打开完整节点档案", key="graph:open-analysis-node"):
                _navigate_node(selected)
                st.rerun()
            if st.button("查看断言与证据", key="graph:open-node-assertions"):
                st.session_state["graph:pending-page"] = "实体表"
                st.rerun()

    st.markdown("### 从中心节点探索邻域")
    if visible_nodes:
        center_id = st.selectbox(
            "中心节点",
            [node["id"] for node in visible_nodes],
            format_func=lambda identity: _node_label(next(row for row in visible_nodes if row["id"] == identity), visible_nodes),
            key="graph:neighborhood-center",
        )
        depth_col, types_col = st.columns([1, 3])
        depth = depth_col.slider("关系深度", min_value=1, max_value=3, value=2, key="graph:neighborhood-depth")
        endpoint_types = types_col.multiselect(
            "终点类型",
            list(TYPE_LABELS),
            default=list(TYPE_LABELS),
            format_func=lambda value: TYPE_LABELS[value],
            key="graph:neighborhood-types",
        )
        try:
            neighborhood = ops.graph_neighborhood(
                center_id,
                depth=int(depth),
                node_types=endpoint_types,
                context_id=params.get("context_id", "canon"),
                story_cut=params.get("story_cut"),
                disclosure_cut=params.get("disclosure_cut"),
            )
        except (OperationError, ValueError) as exc:
            st.error(str(exc))
            return
        neighbor_nodes = neighborhood.get("nodes", [])
        if not neighbor_nodes:
            center_status = {
                "missing": "中心节点已不存在。",
                "not_visible_or_truncated": "当前时间或披露范围内看不到这个中心节点。",
            }.get(neighborhood.get("center_status"), "这个阅读范围内没有可见邻域。")
            st.info(center_status)
        else:
            st.caption(f"修订 {neighborhood.get('revision')} · 深度 {neighborhood.get('depth')} · {len(neighbor_nodes)} 个可见节点")
            if neighborhood.get("truncated"):
                st.warning("邻域结果达到显示上限，缩小类型或深度可查看更完整的范围。")
            st.graphviz_chart(_graphviz(neighborhood, list(TYPE_LABELS)), width="stretch")
        paths = neighborhood.get("paths", [])
        st.markdown(f"#### 可达路径（{len(paths)}）")
        if paths:
            path_objects = {row["id"]: row for row in neighbor_nodes}
            for index, path in enumerate(paths, start=1):
                steps = path.get("steps", [])
                pieces = [_obj_label(path_objects.get(center_id))]
                for step in steps:
                    edge = step.get("edge", {})
                    next_id = step.get("to_id")
                    predicate = edge.get("label") or edge.get("semantic") or "关联"
                    role = f"（角色：{edge['role']}）" if edge.get("role") else ""
                    if step.get("traversal") == "reverse":
                        pieces.append(f"←{predicate}{role}— {_obj_label(path_objects.get(next_id))}")
                    else:
                        pieces.append(f"—{predicate}{role}→ {_obj_label(path_objects.get(next_id))}")
                path_kind = "直接断言" if path.get("direct") else "推导路径"
                st.write(f"{index}. [{path_kind}] " + " ".join(pieces))
                st.caption(" · ".join(filter(None, [
                    "含主张或推断路径" if path.get("contains_claim_or_inference") else "依据可见断言连接",
                    f"{path.get('length', len(steps))} 跳",
                ])))
            if any(not path.get("direct") for path in paths):
                st.info("多跳路线是按已记录断言串联出的推导路径，不等同于一条直接事实；单跳路线仍标为直接断言。每一步保留原始方向、谓词和角色。")
        elif neighbor_nodes:
            st.caption("没有找到符合终点类型的多跳路径；中心节点本身仍显示在邻域图中。")
        if neighborhood.get("uncertainties"):
            st.warning("部分连接因时间或披露范围未知而未纳入路径。")
            for uncertainty in neighborhood["uncertainties"]:
                st.write(uncertainty.get("reason", "范围未知"))


def _candidate_label(candidate: dict, by_id: dict[str, dict]) -> str:
    objects = candidate.get("objects", [])
    counts: dict[str, int] = {}
    examples: list[str] = []
    for obj in objects:
        kind = obj.get("kind", "object")
        counts[kind] = counts.get(kind, 0) + 1
        if len(examples) < 2:
            examples.append(obj.get("name") or obj.get("label") or _assertion_label(obj, by_id) if kind == "assertion" else obj.get("name") or obj.get("label") or kind)
    summary = "、".join(f"{count}{_kind_label(kind)}" for kind, count in counts.items()) or "空批次"
    preview = "；".join(examples)
    return f"{summary} · {preview} · {candidate.get('status', '待审核')}"


def _kind_label(kind: str) -> str:
    return {"node": "节点", "assertion": "断言", "predicate": "谓词", "evidence": "证据", "evidence_link": "证据链接", "context": "语境", "timeline": "时间轴", "source_order": "披露顺序"}.get(kind, kind)


def _review_page(ops, snapshot: dict, sources: list[dict]) -> None:
    st.header("提取成果 · 候选审核")
    candidates = ops.graph_candidates()
    if not candidates:
        st.info("目前没有分析候选。待审核对象不会进入分析检索或世界观。")
        return
    objects = {obj["id"]: obj for obj in snapshot.get("objects", [])}
    sources_by_id = {row["id"]: row for row in sources}
    previews = {row["id"]: ops.graph_candidate_preview(row["id"]) for row in candidates}

    def preview_label(identity: str) -> str:
        preview = previews[identity]
        rows = preview.get("rows", [])
        status = preview.get("candidate", {}).get("status")
        status_label = {"pending": "待审核", "accepted": "已采纳", "rejected": "已拒绝"}.get(status, status)
        examples = [row.get("qualified_label") or row.get("label", "") for row in rows[:2]]
        counts: dict[str, int] = {}
        for row in rows:
            kind_label = row.get("kind_label", "对象")
            counts[kind_label] = counts.get(kind_label, 0) + 1
        count_text = "、".join(f"{count}{kind}" for kind, count in counts.items()) or "空批次"
        return f"{count_text} · {'；'.join(examples)} · {status_label}"

    selected_id = st.selectbox("候选批次", [row["id"] for row in candidates], format_func=preview_label, key="graph:review-candidate")
    preview = previews[selected_id]
    candidate = preview.get("candidate") or ops.graph_candidate(selected_id)
    row_by_id = {row.get("id"): row for row in preview.get("rows", [])}
    for obj in candidate.get("objects", []):
        objects[obj["id"]] = obj
    candidate_status = {"pending": "待审核", "accepted": "已采纳", "rejected": "已拒绝"}.get(candidate.get("status"), candidate.get("status"))
    st.caption(f"状态：{candidate_status} · 版本 {candidate.get('version')} · 预期修订 {candidate.get('expected_revision')}")
    try:
        candidate_hierarchy = preview.get("hierarchy") or ops.graph_hierarchy(candidate_id=selected_id)
    except (OperationError, ValueError) as exc:
        st.caption(f"此候选暂不能生成层级预览：{exc}")
    else:
        with st.expander("候选事件层级 · 仅预览", expanded=bool(candidate_hierarchy.get("edges"))):
            for line in _hierarchy_lines(candidate_hierarchy):
                st.text(line)
            shared_lines = _hierarchy_lines(candidate_hierarchy, shared=True)
            if shared_lines:
                st.caption(f"人物、地点与物件（{len(shared_lines)}）")
                for line in shared_lines:
                    st.text(line)
    for obj in candidate.get("objects", []):
        display = row_by_id.get(obj.get("id"), {})
        if display.get("summary"):
            st.write(display["summary"])
        if obj.get("kind") == "node":
            st.markdown(f"**{TYPE_LABELS.get(obj.get('node_type'), '节点')}：{obj.get('name')}**")
            if obj.get("aliases"):
                st.caption("别名：" + "、".join(obj["aliases"]))
        elif obj.get("kind") == "assertion":
            st.write(f"{_assertion_label(obj, objects)} · {SCOPE_LABELS.get(obj.get('scope'), obj.get('scope'))} · {ASSESSMENT_LABELS.get(obj.get('assessment'), obj.get('assessment'))}")
            if obj.get("holder_id"):
                st.caption("持有人：" + _ref(obj["holder_id"], objects))
            if obj.get("validity"):
                st.caption("有效时间：" + _time_extent(obj["validity"], objects))
        elif obj.get("kind") == "evidence":
            st.info(display.get("summary") or _evidence_text(obj, sources_by_id))
        elif obj.get("kind") == "evidence_link":
            st.caption(f"证据关联：{obj.get('role', 'supports')}")
        else:
            st.write(f"{_kind_label(obj.get('kind', '对象'))}：{display.get('qualified_label') or display.get('label') or obj.get('label') or obj.get('name') or '未命名对象'}")
        with st.expander("技术详情 · 候选对象"):
            st.json(obj)
    diagnostics = preview.get("diagnostics", candidate.get("diagnostics", []))
    if diagnostics:
        st.markdown("#### 预检记录")
        for diagnostic in diagnostics:
            (st.error if diagnostic.get("severity") == "error" else st.warning)(diagnostic.get("message", "诊断"))
    if candidate.get("status") == "pending":
        accept, reject = st.columns(2)
        with accept:
            if st.button("接受并进入正式图", key=f"accept:{selected_id}"):
                try:
                    revision = ops.graph_accept(selected_id, candidate["version"])
                    st.success(f"已接受，正式修订为 {revision}。")
                    st.rerun()
                except (OperationError, ValueError) as exc:
                    st.error(str(exc))
        if st.button("交给 Agent 修订此候选", key=f"revise:{selected_id}"):
            st.session_state["graph:agent-seed-id"] = selected_id
            st.session_state["graph:pending-page"] = "提取"
            st.rerun()
        with reject:
            if st.button("拒绝此候选", key=f"reject:{selected_id}"):
                try:
                    ops.graph_reject(selected_id, candidate["version"])
                    st.success("已拒绝；正式图未改变。")
                    st.rerun()
                except (OperationError, ValueError) as exc:
                    st.error(str(exc))


def _analysis_settings(ops) -> dict:
    settings = ops.harness_settings()
    with st.expander("模型服务配置", expanded=False):
        catalog = ops.provider_catalog()
        providers = [item["id"] for item in catalog]
        provider = st.selectbox("服务", providers, index=providers.index(settings["provider"]) if settings["provider"] in providers else 0, format_func=lambda item: next((row["name"] for row in catalog if row["id"] == item), item), key="graph:provider")
        selected = ops.harness_settings(provider)
        profile_identity = (ops.project_info().path, provider)
        if st.session_state.get("graph:settings-profile") != profile_identity:
            for key, value in {
                "graph:model": selected["model"], "graph:base-url": selected["base_url"],
                "graph:key-env": selected["key_env"], "graph:token-param": selected["token_parameter"],
                "graph:service-default": selected.get("max_tokens") is None,
                "graph:max-tokens": max(1, int(selected.get("max_tokens") or 131072)),
                "graph:timeout": int(selected.get("timeout_seconds", 120)),
                **{
                    f"graph:{field}": int(selected.get(field, default))
                    for field, default in (
                        ("context_window", 1_000_000), ("output_reserve", 131_072),
                        ("compaction_reserve", 65_536), ("compaction_max_tokens", 32_768),
                    )
                },
            }.items():
                st.session_state[key] = value
            st.session_state["graph:settings-profile"] = profile_identity
        model = st.text_input("模型 ID", value=selected["model"], key="graph:model")
        base_url = st.text_input("兼容 API 地址", value=selected["base_url"], key="graph:base-url")
        key_env = st.text_input("密钥环境变量名", value=selected["key_env"], key="graph:key-env")
        token_parameter = st.selectbox("输出上限参数", ["max_tokens", "max_completion_tokens"], index=0 if selected["token_parameter"] == "max_tokens" else 1, key="graph:token-param")
        default_limit = selected.get("max_tokens")
        unlimited = st.checkbox("不发送输出上限，由服务决定", value=default_limit is None, key="graph:service-default")
        max_tokens = None if unlimited else st.number_input("单次响应输出上限", min_value=1, value=max(1, int(default_limit or 131072)), step=1024, key="graph:max-tokens")
        context_settings = {
            field: int(st.number_input(
                label, min_value=0 if field in {"output_reserve", "compaction_reserve"} else 1,
                step=1, key=f"graph:{field}",
            ))
            for field, label in (
                ("context_window", "上下文窗口（token）"),
                ("output_reserve", "输出预留（token）"),
                ("compaction_reserve", "压缩预留（token）"),
                ("compaction_max_tokens", "压缩最大输出（token）"),
            )
        }
        history_budget = context_settings["context_window"] - max(context_settings["output_reserve"], int(max_tokens or 0)) - context_settings["compaction_reserve"]
        st.caption(f"可用历史阈值：{max(0, history_budget):,} token。单次生成上限仍独立设置。")
        st.caption("有效输出预留取输出预留与单次生成上限的较大值。")
        if history_budget <= 0:
            st.error("上下文窗口须大于有效输出预留与压缩预留之和。")
        timeout = st.number_input("单次请求超时（秒）", min_value=5, max_value=600, value=int(selected.get("timeout_seconds", 120)), step=5, key="graph:timeout")
        if st.button("保存模型配置", key="graph:save-settings", disabled=history_budget <= 0):
            data = selected | {"provider": provider, "model": model, "base_url": base_url, "key_env": key_env, "token_parameter": token_parameter, "max_tokens": None if max_tokens is None else int(max_tokens), "timeout_seconds": int(timeout), **context_settings}
            try:
                st.session_state["graph:saved-settings"] = ops.save_harness_settings(data)
                st.success("模型配置已保存。密钥只从环境变量读取。")
                st.rerun()
            except (OperationError, ValueError) as exc:
                st.error(str(exc))
        source_info = next((row for row in catalog if row["id"] == provider), {})
        if source_info.get("docs_url"):
            st.markdown(f"[供应商配置说明]({source_info['docs_url']})")
    return settings


def _agent_page(ops, sources: list[dict]) -> None:
    st.header("提取 · 可观察分析代理")
    st.caption("代理可读取已选原文和正式图，生成私有草稿；只有通过预检的批次才会进入「提取成果」等待人工审核。")
    settings = _analysis_settings(ops)
    seed_id = st.session_state.get("graph:agent-seed-id")
    seed_label = None
    if seed_id:
        try:
            preview = ops.graph_candidate_preview(seed_id)
            seed_label = "、".join(row.get("qualified_label") or row.get("label", "") for row in preview.get("rows", [])[:3])
            st.info(f"修订起点：{seed_label or '未命名候选'} · 父批次保持原状态，新运行只生成新的待审候选。")
        except (OperationError, ValueError):
            st.error("修订起点候选已无法读取，请从当前待审列表重新选择。")
            seed_id = None
    if not sources:
        st.info("先导入至少一章原文，再启动分析。")
    source_ids = [row["id"] for row in sources]
    if st.session_state.pop("graph:reset-agent-feedback", False):
        st.session_state["graph:agent-feedback"] = ""
    with st.form("graph:agent-start"):
        chosen_sources = st.multiselect("分析来源", source_ids, default=source_ids if seed_id else source_ids[:1], format_func=lambda identity: next((row["title"] for row in sources if row["id"] == identity), identity), key="graph:agent-sources")
        default_task = "依据审核意见补证并修订人物、事件、关系和时序；保留未知，不把推断提升为事实。" if seed_id else "建立可核验的节点、关系、事件、时序和人物主张；保留未知，不把推断提升为事实。"
        task = st.text_area("本次分析目标", value=default_task, key="graph:agent-task")
        feedback = st.text_area("修订意见", placeholder="说明缺少的证据、需要拆分的对象或应保留的未知；不修订时留空。", key="graph:agent-feedback", disabled=not bool(seed_id))
        research_rounds = st.number_input("检索阶段建议模型轮数", min_value=0, value=3, step=1, key="graph:agent-research-rounds")
        output_mode = st.selectbox("单轮输出容量", ["继承工程配置", "服务默认（不发送上限参数）", "本次覆盖"], key="graph:agent-output-mode")
        override_tokens = st.number_input("本次单轮输出上限", min_value=1, value=int(settings.get("max_tokens") or 8192), step=1024, disabled=output_mode != "本次覆盖", key="graph:agent-override-tokens")
        begin = st.form_submit_button("启动图分析")
    if begin:
        if not chosen_sources:
            st.error("至少选择一个原文来源。")
        else:
            budget: dict = {"research_rounds": int(research_rounds)}
            if output_mode == "服务默认（不发送上限参数）":
                budget["max_tokens_per_round"] = "service-default"
            elif output_mode == "本次覆盖":
                budget["max_tokens_per_round"] = int(override_tokens)
            else:
                budget["max_tokens_per_round"] = "inherit"
            try:
                run_id = ops.start_graph_analysis(chosen_sources, task=task, feedback=feedback.strip() or None if seed_id else None, seed_candidate_id=seed_id, budget=budget)
                st.session_state["graph:run-id"] = run_id
                st.session_state[f"graph:run-sources:{run_id}"] = [next((row["title"] for row in sources if row["id"] == sid), "来源未知") for sid in chosen_sources]
                if seed_id:
                    st.session_state[f"graph:run-parent:{run_id}"] = seed_label
                    st.session_state.pop("graph:agent-seed-id", None)
                    st.session_state["graph:reset-agent-feedback"] = True
                st.success("分析已启动。暂停、补充指令和取消都通过项目操作接口提交。")
                st.rerun()
            except (OperationError, ValueError, RuntimeError) as exc:
                st.error(str(exc))
    runs = ops.graph_analysis_runs()
    source_titles = {row["id"]: row["title"] for row in sources}
    run_ids = [row["run_id"] for row in runs]
    preferred = st.session_state.get("graph:run-id")
    if run_ids:
        selected_run = st.selectbox(
            "分析运行记录",
            run_ids,
            index=run_ids.index(preferred) if preferred in run_ids else 0,
            format_func=lambda identity: _run_label(next(row for row in runs if row["run_id"] == identity), source_titles),
            key="graph:run-history",
        )
        st.session_state["graph:run-id"] = selected_run
    run_id = st.session_state.get("graph:run-id")
    if not run_id:
        st.info("尚无本次会话启动的分析任务。")
        return
    auto_refresh = st.checkbox("自动刷新运行轨迹（每 2 秒）", value=True, key="graph:auto-refresh")
    current = ops.graph_analysis_trace(run_id)
    active = current.get("status") in {"queued", "running"}
    external_unverified = current.get("observer_state") == "unverified_external"
    if auto_refresh and active and not external_unverified:
        _render_agent_run_live(ops, run_id, sources)
    else:
        _render_agent_run(ops, run_id, sources, current)


def _run_label(run: dict, source_titles: dict[str, str]) -> str:
    status_value = str(run.get("status", "未知"))
    phase_value = str(run.get("phase", ""))
    status = {"queued": "排队中", "running": "运行中", "complete": "已完成", "incomplete": "未完成", "cancelled": "已取消", "interrupted": "中断"}.get(status_value, status_value)
    phase = {"research": "检索", "draft": "起草", "correction": "修正", "submit": "提交预检", "complete": "完成"}.get(phase_value, phase_value)
    chapters = "、".join(source_titles.get(identity, "来源未知") for identity in run.get("source_ids", [])) or "无原文"
    changed = datetime.fromtimestamp(run.get("modified_at", 0)).strftime("%m-%d %H:%M")
    return f"{status} · {phase} · {run.get('model', '未知模型')} · {chapters} · {run.get('rounds', 0)} 轮 · {changed}"


def _usage_summary(usage: dict | None) -> str:
    usage = usage if isinstance(usage, dict) else {}
    total = usage.get("total_tokens")
    if usage.get("incomplete"):
        if isinstance(total, int) and total > 0:
            return f"Usage：至少 {total} tokens（用量记录不完整）"
        return "Usage：未报告（用量记录不完整）"
    if total is None:
        return "Usage：未报告"
    return f"Usage：{total} tokens"


def _instruction_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


@st.fragment(run_every="2s")
def _render_agent_run_live(ops, run_id: str, sources: list[dict]) -> None:
    _render_agent_run(ops, run_id, sources)


def _render_agent_run(ops, run_id: str, sources: list[dict], trace: dict | None = None) -> None:
    trace = trace or ops.graph_analysis_trace(run_id)
    status = trace.get("status", "unknown")
    status_label = {"queued": "排队中", "running": "运行中", "complete": "已完成", "incomplete": "未完成", "cancelled": "已取消", "interrupted": "中断"}.get(status, status)
    phase_value = str(trace.get("phase", "—"))
    phase_label = {"research": "检索", "draft": "起草", "correction": "修正", "submit": "提交预检", "complete": "完成"}.get(phase_value, phase_value)
    st.markdown(f"#### 当前任务：{status_label} · 阶段：{phase_label}")
    st.write(trace.get("task") or "未填写任务目标")
    lineage = trace.get("lineage") or {}
    parent_id = lineage.get("parent_candidate_id") or trace.get("seed_candidate_id")
    if parent_id:
        try:
            parent_preview = ops.graph_candidate_preview(parent_id)
            parent_rows = parent_preview.get("rows", [])
            parent_name = "、".join((row.get("qualified_label") or row.get("label", "")) for row in parent_rows[:3]) or "候选批次"
            st.info(f"修订自：{parent_name}。父批次保持原样；本运行会产生独立子候选。")
        except (OperationError, ValueError):
            st.info("此运行基于先前候选修订；父候选当前无法读取。")
        feedback = trace.get("feedback")
        if feedback:
            origins = {"review_agent_guided": "独立预审意见引导", "user_input": "用户填写的修订意见", "user_edited_review": "用户修改过的预审意见"}
            origin_key = trace.get("feedback_origin")
            origin = origins.get(origin_key) if isinstance(origin_key, str) else None
            origin = origin or "修订意见"
            st.markdown(f"**{origin}：** {feedback}")
        with st.expander("技术详情 · 父候选与反馈标识"):
            st.json({"parent_candidate_id": parent_id, "lineage": lineage, "feedback_origin": trace.get("feedback_origin")})
    source_titles = {row["id"]: row["title"] for row in sources}
    selected_titles = [source_titles.get(item, "来源未知") for item in trace.get("source_ids", [])] or st.session_state.get(f"graph:run-sources:{run_id}", [])
    if selected_titles:
        st.caption("来源：" + "、".join(selected_titles))
    request_value = str(trace.get("request_state", "未知"))
    request_state = {"waiting": "等待请求", "in_flight": "模型正在请求", "processing": "本地处理响应", "paused_waiting": "已暂停，等待恢复", "stopped": "请求已停止"}.get(request_value, request_value)
    st.caption(f"请求状态：{request_state} · 供应商：{trace.get('provider', '未知')} · 模型：{trace.get('model', '未知')} · 提示版本：{trace.get('prompt_version', '未知')} · 模型请求：{trace.get('rounds', 0)} · 工具动作：{trace.get('tool_count', 0)}")
    st.caption(_usage_summary(trace.get("usage")) + " · 状态以任务冻结配置为准")
    settings = trace.get("frozen_service") or trace.get("config") or {}
    if settings:
        st.caption(f"冻结输出设置：{trace.get('output_limit_mode', settings.get('output_limit_mode', '历史配置'))} · {trace.get('output_token_parameter', settings.get('token_parameter', '输出参数'))}={trace.get('output_token_value', settings.get('max_tokens', '未记录'))}")
    if trace.get("observer_state") == "unverified_external":
        st.warning("上次记录仍显示运行中，但当前工作台无法确认外部进程是否仍在执行。")
    if trace.get("error"):
        st.error(trace["error"])
    controls = trace.get("control", {})
    live = status in {"queued", "running"}
    cols = st.columns(4)
    if cols[0].button("刷新轨迹", key=f"refresh:{run_id}"):
        st.rerun()
    if cols[1].button("暂停", key=f"pause:{run_id}", disabled=not live or controls.get("paused", False)):
        ops.pause_graph_analysis(run_id)
        st.rerun()
    if cols[2].button("恢复", key=f"resume:{run_id}", disabled=not live or not controls.get("paused", False)):
        ops.resume_graph_analysis(run_id)
        st.rerun()
    if cols[3].button("请求取消", key=f"cancel:{run_id}", disabled=not live):
        ops.cancel_graph_analysis(run_id)
        st.rerun()
    st.caption("暂停在下一次模型请求前生效；正在发送的请求不会被强行中断。取消也是协作式收尾。")
    with st.form(f"instruction:{run_id}"):
        instruction = st.text_input("补充分析指令")
        send = st.form_submit_button("发送指令", disabled=not live)
    if send and instruction.strip():
        ops.instruct_graph_analysis(run_id, instruction.strip())
        st.success("指令已排队，代理会在后续请求边界读取。")
        st.rerun()
    recorded_instructions = controls.get("instructions", [])
    if recorded_instructions:
        st.markdown("##### 已记录的补充指令")
        for item in recorded_instructions:
            consumed_at = item.get("consumed_at")
            state = "已发送到后续模型请求" if consumed_at else "已排队，等待后续模型请求"
            st.info(f"第 {item.get('seq', '?')} 条 · {state}：{item.get('text', '')}")
            times = []
            created_at = _instruction_timestamp(item.get("created_at"))
            sent_at = _instruction_timestamp(consumed_at)
            if created_at:
                times.append(f"排队时间：{created_at}")
            if sent_at:
                times.append(f"发送时间：{sent_at}")
            if times:
                st.caption(" · ".join(times))
        st.caption("状态来自运行记录；发送到模型请求不表示模型已采纳或执行指令。")
    workspace = trace.get("workspace", {})
    workspace_count = len(workspace) if isinstance(workspace, (dict, list)) else 0
    st.write(f"私有草稿：{workspace_count} 项 · 精确证据句柄：{trace.get('evidence_handles', 0)} 个")
    diagnostics = trace.get("precheck_diagnostics", [])
    if diagnostics:
        st.markdown("##### 最近预检诊断")
        for row in diagnostics:
            (st.error if row.get("severity") == "error" else st.warning)(row.get("message", str(row)))
    for step in trace.get("steps", []):
        phase = {"research": "检索", "draft": "起草", "correction": "修正", "submit": "预检／提交"}.get(step.get("phase"), step.get("phase", "轮次"))
        with st.expander(f"第 {step.get('round', '?')} 轮 · {phase} · {step.get('finish_reason') or '未结束'}", expanded=False):
            if step.get("plan"):
                st.write("本轮模型公开说明：" + step["plan"])
            if step.get("usage"):
                st.caption(f"本轮 usage：{step['usage']}")
            tool_labels = {row.get("name"): row.get("label") for row in trace.get("capabilities", {}).get("tools", [])}
            for tool in step.get("tools", []):
                tool_name = tool.get("tool", tool.get("name", "未知工具"))
                st.markdown(f"**{tool_labels.get(tool_name, tool_name)}**")
                if tool.get("args"):
                    st.caption(f"参数：{tool['args']}")
                result = tool.get("result", {})
                if isinstance(result, dict):
                    if result.get("title"):
                        st.caption(result["title"])
                    if result.get("diagnostics"):
                        for diagnostic in result["diagnostics"]:
                            st.warning(diagnostic.get("message", str(diagnostic)))
                    if result.get("entity_types"):
                        st.caption("观察到类型：" + "、".join(map(str, result["entity_types"])))
                    if result.get("summary"):
                        st.write(result["summary"])
                    elif result.get("preview"):
                        st.write(result["preview"])
                    elif result.get("error"):
                        st.warning(result["error"])
                    else:
                        st.write(result)
                else:
                    st.write(result)
            st.caption(f"新增草稿：{len(step.get('stage_ids', step.get('staged', [])))} 项 · 撤回：{len(step.get('removed_stage_ids', step.get('removed', [])))} 项")
            for diagnostic in step.get("diagnostics", []) + step.get("stage_diagnostics", []):
                st.warning(diagnostic.get("message", str(diagnostic)))
            with st.expander("技术详情 · 本轮模型实际响应（不含私有推理通道）"):
                try:
                    response = ops.graph_analysis_response(run_id, int(step["round"]))
                    st.code(response.get("raw", ""), language="json")
                    st.json({key: response.get(key) for key in ("phase", "finish_reason", "usage", "prompt_version", "service_error")})
                except (AttributeError, OperationError, ValueError):
                    st.info("本轮尚无已记录的完整响应。")
    for candidate_id in trace.get("candidate_ids", []):
        try:
            candidate = ops.graph_candidate(candidate_id)
        except (OperationError, ValueError):
            st.warning("运行记录引用的候选当前无法读取。")
            continue
        refs = {row["id"]: row for row in ops.graph_snapshot().get("objects", [])}
        refs.update({row.get("id"): row for row in candidate.get("objects", []) if row.get("id")})
        candidate_status = {"pending": "待审核", "accepted": "已采纳", "rejected": "已拒绝"}.get(candidate.get("status"), candidate.get("status"))
        st.info(f"候选当前状态：{candidate_status} · {_candidate_label(candidate, refs)}")


def _entity_page(ops, snapshot: dict, sources: list[dict]) -> None:
    st.header("实体表 · 节点档案")
    nodes = snapshot.get("nodes", [])
    node_types = sorted({node.get("node_type", "other") for node in nodes})
    selected_types = st.multiselect("筛选类型", node_types, default=node_types, format_func=lambda value: TYPE_LABELS.get(str(value), str(value)), key="graph:entity-types")
    visible = [row for row in nodes if row.get("node_type") in selected_types]
    selected_id = _node_selector(visible, "选择节点", "graph:entity-select", st.session_state.get("graph:selected_node"))
    if selected_id:
        st.session_state["graph:selected_node"] = selected_id
        _render_node_detail(selected_id, snapshot, {row["id"]: row for row in sources})


def _timeline_page(snapshot: dict) -> None:
    st.header("时间轴")
    objects = {row["id"]: row for row in snapshot.get("objects", [])}
    events = [row for row in snapshot.get("nodes", []) if row.get("node_type") == "event"]
    if not events:
        st.info("尚无已审核事件。事件时间未知时会保留为未知，不会被排成确定顺序。")
    temporal = [row for row in snapshot.get("assertions", []) if objects.get(row.get("predicate_id"), {}).get("semantic") == "temporal"]
    rows = []
    for event in events:
        rows.append({"事件": event.get("name"), "类型": event.get("subtype") or "事件", "发生区间": _time_extent(event.get("occurrence"), objects)})
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    st.markdown("#### 已审核先后断言")
    if not temporal:
        st.info("尚无明确先后断言。无序不代表同时发生。")
    for assertion in temporal:
        st.write(f"{_assertion_label(assertion, objects)} · {_time_extent(assertion.get('validity'), objects)} · {ASSESSMENT_LABELS.get(assertion.get('assessment'), '尚未判定')}")
    st.markdown("#### 未知时间")
    unknown = [row for row in events if not row.get("occurrence") or row["occurrence"].get("start", {}).get("state") == "unknown" or row["occurrence"].get("end", {}).get("state") == "unknown"]
    if unknown:
        st.write("、".join(row.get("name", "未命名事件") for row in unknown) + "：发生边界仍未知。")
    else:
        st.caption("没有标为未知的事件边界。")


def _worldview_page(snapshot: dict) -> None:
    st.header("世界观")
    objects = {row["id"]: row for row in snapshot.get("objects", [])}
    sources_by_id = {row["id"]: row for row in snapshot.get("sources", [])}
    world = [row for row in snapshot.get("assertions", []) if row.get("scope") == "world"]
    claims = [row for row in snapshot.get("assertions", []) if row.get("scope") in {"claim", "knowledge"}]
    st.markdown(f"#### 世界事实（{len(world)}）")
    for assertion in world:
        st.write(_assertion_label(assertion, objects) + " · " + ASSESSMENT_LABELS.get(assertion.get("assessment"), "尚未判定"))
    st.markdown(f"#### 人物主张与认知（{len(claims)}）")
    for assertion in claims:
        st.write(_assertion_label(assertion, objects) + f" · {SCOPE_LABELS.get(assertion.get('scope'))} · 持有人：{_ref(assertion.get('holder_id'), objects)}")
    with st.expander("涉及的证据"):
        for evidence in snapshot.get("evidences", []):
            st.write(_evidence_text(evidence, sources_by_id))


def _assets_page(snapshot: dict, sources: list[dict]) -> None:
    st.header("资产库")
    items = [row for row in snapshot.get("nodes", []) if row.get("node_type") in {"item", "location", "scene", "group", "other"}]
    st.caption("本页汇集已审核的物品、地点、场景等节点，以及原文和证据锚；未审核候选不会显示在正式资产中。")
    if items:
        st.dataframe([{"类型": TYPE_LABELS.get(row.get("node_type"), "节点"), "名称": row.get("name"), "别名": "、".join(row.get("aliases", [])), "说明": row.get("identity_note", "")} for row in items], hide_index=True, width="stretch")
    else:
        st.info("暂无物品、地点、场景或群体节点；它们会与人物和事件共用同一节点图。")

    st.markdown(f"#### 已导入原文（{len(sources)}）")
    if not sources:
        st.info("还没有原文。可在「原文证据」导入章节；资产库会显示来源与其证据锚点。")
        return
    evidence_by_id = {row["id"]: row for row in snapshot.get("evidences", [])}
    links_by_evidence: dict[str, list[dict]] = {}
    for link in snapshot.get("evidence_links", []):
        links_by_evidence.setdefault(link.get("evidence_id", ""), []).append(link)
    objects = {row["id"]: row for row in snapshot.get("objects", [])}
    assertions = {row["id"]: row for row in snapshot.get("assertions", [])}
    for source in sources:
        with st.expander(f"{source.get('title', '未命名章节')} · {source.get('length', 0)} 字", expanded=False):
            evidence_rows = [row for row in evidence_by_id.values() if row.get("source_id") == source["id"]]
            st.caption(f"已挂接 {len(evidence_rows)} 个证据锚")
            if not evidence_rows:
                st.info("这份原文目前没有已审核证据锚。")
            for evidence in sorted(evidence_rows, key=lambda row: (row.get("start", 0), row.get("end", 0))):
                st.write(f"字符 {evidence.get('start')}–{evidence.get('end')}：{evidence.get('quote', '')}")
                for link in links_by_evidence.get(evidence["id"], []):
                    assertion = assertions.get(link.get("assertion_id"))
                    if assertion:
                        st.caption(f"{link.get('role', 'supports')} · {_assertion_label(assertion, objects)}")
            if st.button("在原文与证据中打开", key=f"asset-source:{source['id']}"):
                st.session_state["graph:pending-page"] = "原文证据"
                st.session_state["graph:pending-source"] = source["id"]
                st.rerun()


def _sources_page(ops, sources: list[dict]) -> None:
    st.header("原文与证据")
    _source_import(ops)
    if not sources:
        return
    selected = st.selectbox("查看章节", [row["id"] for row in sources], format_func=lambda identity: next((row["title"] for row in sources if row["id"] == identity), "章节"), key="graph:source-view")
    source = ops.source(selected)
    st.caption(f"{len(source['text'])} 个字符 · SHA-256：{source['hash']}")
    st.text_area("不可变原文", source["text"], height=450, disabled=True, key="graph:source-text")
    snapshot = ops.graph_snapshot()
    evidence_objects = {row["id"]: row for row in snapshot.get("evidences", [])}
    linked = [row for row in evidence_objects.values() if row.get("source_id") == selected]
    if linked:
        st.markdown(f"#### 已审核证据片段（{len(linked)}）")
        for row in linked:
            st.info(f"字符 {row['start']}–{row['end']}：{row['quote']}")


def _project(ops) -> None:
    info = ops.graph_project_info()
    st.title(info.get("title") or "未命名作品")
    st.caption(f"v{info.get('schema_version')} · 正式修订 {info.get('revision')} · {info.get('source_count')} 章原文")
    with st.sidebar:
        st.header("当前工程")
        st.write(info.get("title", "未命名作品"))
        st.caption(Path(info.get("path", "")).name)
        if st.button("返回项目首页", key="graph:home"):
            st.session_state.pop("graph:path", None)
            st.rerun()
        pending_page = st.session_state.pop("graph:pending-page", None)
        if pending_page in PAGES:
            st.session_state["graph:page"] = pending_page
            st.session_state["graph:nav"] = pending_page
        pending_node = st.session_state.pop("graph:pending-node", None)
        if pending_node:
            st.session_state["graph:entity-select"] = pending_node
        pending_source = st.session_state.pop("graph:pending-source", None)
        if pending_source:
            st.session_state["graph:source-view"] = pending_source
        page = st.radio("工作区", PAGES, index=PAGES.index(st.session_state.get("graph:page", "分析检索")), key="graph:nav")
        st.session_state["graph:page"] = page
    snapshot = ops.graph_snapshot()
    sources = ops.sources()
    if page == "分析检索":
        _analysis_page(ops, snapshot, sources)
    elif page == "提取":
        _source_import(ops)
        _agent_page(ops, sources)
    elif page == "提取成果":
        _review_page(ops, snapshot, sources)
    elif page == "资产库":
        _assets_page(snapshot, sources)
    elif page == "世界观":
        _worldview_page(snapshot)
    elif page == "实体表":
        _entity_page(ops, snapshot, sources)
    elif page == "时间轴":
        _timeline_page(snapshot)
    elif page == "原文证据":
        _sources_page(ops, sources)


def main() -> None:
    st.set_page_config(page_title="叙事图工作台", page_icon="🕸️", layout="wide")
    if "graph:path" not in st.session_state:
        _home()
        return
    try:
        ops = Operations.open_graph_project(st.session_state["graph:path"])
    except (OperationError, ValueError, OSError) as exc:
        st.error(f"无法打开 v4 工程：{exc}")
        st.info("历史工程不会在此处自动转换。返回首页并创建一个新的 v4 工程。")
        if st.button("返回项目首页"):
            st.session_state.pop("graph:path", None)
            st.rerun()
        return
    try:
        _project(ops)
    except (OperationError, ValueError) as exc:
        st.error(str(exc))

