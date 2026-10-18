import html
import json
import os
import re
from pathlib import Path
from typing import Literal

import streamlit as st

from narrative.model_catalog import MODEL_CATALOG_SOURCES, model_options
from narrative.operations import OperationError, Operations

DATA = Path(os.environ.get("NARRATIVE_PROJECT_DIR", "data")).resolve()
LABELS = {"world": "世界事实", "claim": "人物主张", "knowledge": "人物认知"}
STATE_LABELS = {
    "known": "有据可查",
    "unknown": "未知",
    "conflict": "冲突",
    "future": "尚未生效",
    "expired": "已失效",
    "forgotten": "已遗忘",
}
ATTITUDES = {
    "unknown": "未知",
    "informed": "听说",
    "knows": "知晓",
    "believes": "相信",
    "suspects": "怀疑",
    "denies": "否认",
    "forgotten": "遗忘",
}


def value_text(value):
    if value is None:
        return "未知"
    if type(value) is bool:
        return "是" if value else "否"
    return str(value)


def remember_model_draft(provider: str, widget_key: str, drafts_key: str):
    drafts = dict(st.session_state.get(drafts_key, {}))
    drafts[provider] = st.session_state.get(widget_key, "")
    st.session_state[drafts_key] = drafts


def remember_number_draft(widget_key: str, draft_key: str):
    st.session_state[draft_key] = st.session_state.get(widget_key, 8192)


def display_item_label(display_map: dict, ref: str | None, fallback: str = "未知对象") -> str:
    if not ref:
        return fallback
    item = display_map.get("items", {}).get(ref)
    if item:
        return item.get("qualified_label") or item.get("label") or fallback
    # AppTest may feed a formatted human label back as the widget value.
    # Preserve that readable label, but never surface an unresolved ASCII ID.
    return ref if any(ord(character) > 127 for character in ref) else fallback


def candidate_label(operations: Operations, candidate_id: str, display_map: dict) -> str:
    """Build a human-readable batch label while keeping the selector value stable."""
    try:
        preview = operations.candidate_preview(candidate_id)
    except OperationError:
        return display_item_label(display_map, candidate_id, "待审核候选")
    cards = preview.get("cards", [])
    pieces: list[str] = []
    for kind in ("entity", "event", "relation", "storyline", "classification", "assertion"):
        labels = list(dict.fromkeys(
            card.get("title", "") for card in cards if card.get("kind") == kind and card.get("title")
        ))
        if labels:
            kind_label = {
                "entity": "人物", "event": "事件", "relation": "关系",
                "storyline": "故事线", "classification": "分类", "assertion": "特点/主张",
            }[kind]
            pieces.append(f"{kind_label}：{'、'.join(labels[:2])}")
    if not pieces:
        pieces = [f"{len(cards)} 项待审核资料"]
    return f"{len(cards)}项 · {'；'.join(pieces)} · 待审核"


@st.fragment(run_every="3s")
def narrative_trace_panel(operations: Operations, run_id: str):
    """Render the public, recorded activity for an agent run and its controls."""
    try:
        trace = operations.narrative_analysis_trace(run_id)
    except ValueError as exc:
        st.error(str(exc))
        return

    phase_labels = {
        "research": "证据检索", "draft": "草拟", "precheck": "预检",
        "correction": "预检修正", "submit": "提交候选", "complete": "完成",
    }
    state_labels = {
        "queued": "排队中", "running": "运行中", "complete": "完成",
        "incomplete": "未完成", "cancelled": "已取消", "interrupted": "已中断",
    }
    request_labels = {
        "not_started": "尚未发起模型请求", "waiting": "等待模型请求",
        "in_flight": "模型正在请求服务", "paused_waiting": "已在请求前暂停",
        "processing": "本地处理模型响应", "completed": "本轮请求已完成",
        "stopped": "请求已停止",
    }
    capabilities = trace.get("capabilities") or {}
    tool_labels = {
        "search_analysis": "组合检索", "search_source": "检索原文",
        "source_excerpt": "查看原文片段", "locate": "定位证据",
        "locate_many": "批量定位证据", "locate_evidence": "定位证据",
        "evidence_excerpt": "查看证据上下文", "object_schema": "读取资料格式",
        "analysis_context": "读取分析上下文", "character_profile": "查看人物档案",
        "event_profile": "查看事件详情", "classification_table": "查看分类表",
        "extraction_observation": "读取旧提取记录",
    }
    tool_labels.update({
        item.get("name"): item.get("label", item.get("name"))
        for item in capabilities.get("tools", []) if item.get("name")
    })
    run_status = trace.get("status")
    phase = trace.get("phase")
    request_state = trace.get("request_state")
    observer_state = trace.get("observer_state")
    worker_local = trace.get("worker_local")
    st.subheader("Harness 实时轨迹")
    st.caption(trace.get("notice") or "仅展示已记录的模型响应与工具动作；不展示或推断模型内部思考。")
    status_text = state_labels.get(str(run_status), str(run_status) if run_status else "未知状态")
    if observer_state == "unverified_external":
        status_text = f"上次记录：{status_text} · 外部执行状态待核实"
    elif worker_local is True:
        st.caption("此任务的工作进程在当前工作台中，可查看最新记录。")
    st.markdown(
        f"**{status_text}** · "
        f"{phase_labels.get(str(phase), str(phase) if phase else '阶段未记录')} · "
        f"{request_labels.get(str(request_state), str(request_state) if request_state else '请求状态未记录')}"
    )
    if observer_state == "unverified_external":
        st.warning("任务由其他工作进程创建或运行；以上是最近一次已记录状态，无法据此确认它现在仍在运行。")
    st.caption(
        f"任务模型：{trace.get('provider') or '未知供应商'} · {trace.get('model') or '未知模型'} · "
        f"提示版本：{trace.get('prompt_version') or '未知'} · "
        f"模型轮次：{trace.get('rounds', 0)} · 工具动作：{trace.get('tool_count', 0)}"
    )
    output_mode = trace.get("output_limit_mode")
    output_value = trace.get("output_token_value")
    output_parameter = trace.get("output_token_parameter")
    output_sent = trace.get("output_parameter_sent")
    output_description = "输出设置未记录"
    output_mode_labels = {
        "inherit": "继承服务设置", "service-default": "服务默认",
        "override": "任务指定", "unknown": "未记录",
    }
    if output_mode:
        if output_sent is False or (output_mode == "service-default" and output_value is None):
            output_description = "未发送上限参数，由服务决定"
        elif output_value is not None:
            output_description = f"{output_value} token · {output_parameter or '输出字段未记录'}"
            if output_sent is False:
                output_description += " · 参数未发送"
        else:
            output_description = "数值未记录"
        st.caption(
            f"每轮输出设置：{output_mode_labels.get(output_mode, output_mode)} · "
            f"{output_description}"
        )
    sources = trace.get("source_labels") or []
    if sources:
        st.caption(f"分析章节：{'、'.join(sources)}")
    if trace.get("task"):
        st.markdown(f"**分析目标：** {trace['task']}")
    if phase == "correction":
        st.info("当前正在根据上一轮校验反馈修正草稿。")
    if phase == "precheck" and any(
        item.get("severity") == "error" for item in (trace.get("precheck_diagnostics") or [])
    ):
        st.warning("业务预检发现问题；这些项目尚不能提交为待审核候选。")
    if run_status == "incomplete" and not trace.get("candidate_ids"):
        st.warning("本次运行未完成，当前没有待审核候选。请查看请求错误或阶段诊断。")

    control = trace.get("control") or {}
    active = run_status in {"queued", "running"} and observer_state != "recorded_terminal"
    if request_state == "in_flight":
        if observer_state == "unverified_external":
            st.info("上次记录显示模型请求在途；当前工作进程不在此界面，无法确认远端请求是否仍在处理。")
        else:
            st.info("模型正在处理当前请求。暂停会在下一次模型请求前生效；取消也会等待当前请求返回，不会强行中断远端服务。")
    elif control.get("paused") or request_state == "paused_waiting":
        st.info("任务已在本地请求边界暂停；可恢复后继续。")
    if active:
        control_cols = st.columns(3)
        if control.get("paused") or request_state == "paused_waiting":
            if control_cols[0].button("恢复任务", key=f"agent-resume-{run_id}"):
                try:
                    operations.resume_narrative_analysis(run_id)
                except ValueError as exc:
                    st.warning(str(exc))
                else:
                    st.rerun()
        else:
            if control_cols[0].button("请求暂停", key=f"agent-pause-{run_id}"):
                try:
                    operations.pause_narrative_analysis(run_id)
                except ValueError as exc:
                    st.warning(str(exc))
                else:
                    st.rerun()
        if control_cols[1].button("请求取消", key=f"agent-cancel-{run_id}"):
            try:
                operations.cancel_narrative_analysis(run_id)
            except ValueError as exc:
                st.warning(str(exc))
            else:
                st.rerun()
        if control_cols[2].button("刷新轨迹", key=f"agent-refresh-{run_id}"):
            st.rerun()
        instruction = st.text_area(
            "补充给 agent 的指令", max_chars=4000,
            key=f"agent-instruction-{run_id}",
        )
        if st.button(
            "发送补充指令", disabled=not instruction.strip(),
            key=f"agent-send-instruction-{run_id}",
        ):
            try:
                operations.send_narrative_instruction(run_id, instruction.strip())
            except ValueError as exc:
                st.warning(str(exc))
            else:
                st.rerun()
        st.caption("补充指令会在后续模型请求时注入；已在途请求不会被改写。")
    else:
        if st.button("刷新轨迹", key=f"agent-refresh-{run_id}"):
            st.rerun()

    usage = trace.get("usage") or {}
    st.caption(
        f"用量：输入 {usage.get('prompt_tokens', 0)} · 输出 {usage.get('completion_tokens', 0)} · "
        f"合计 {usage.get('total_tokens', 0)} · 待处理指令 {control.get('pending_count', 0)}"
    )
    staged = trace.get("workspace") or []
    st.caption(f"当前草稿对象：{len(staged)} · 待审核候选：{len(trace.get('candidate_ids') or [])}")
    if staged:
        st.dataframe(
            [{"草稿对象": row.get("label", "未命名对象"), "类型": row.get("kind", "")} for row in staged],
            hide_index=True, width="stretch",
        )
    if trace.get("precheck_diagnostics"):
        st.markdown("**当前预检问题**")
        st.dataframe(trace["precheck_diagnostics"], hide_index=True, width="stretch")
    capabilities = trace.get("capabilities") or {}
    if capabilities:
        with st.expander("本次任务可调用的工具与审核边界"):
            st.caption(capabilities.get("mode", "工具模式未记录"))
            st.dataframe(
                [{"工具": item.get("label", item.get("name", "工具")),
                  "用途": item.get("purpose", ""),
                  "权限": "只读" if item.get("access") == "read_only" else item.get("access", "")}
                 for item in capabilities.get("tools", [])],
                hide_index=True, width="stretch",
            )
            if capabilities.get("private_actions"):
                st.caption("草稿动作：" + "、".join(capabilities["private_actions"]))
            if capabilities.get("review_gate"):
                st.caption(capabilities["review_gate"])
    if control.get("instructions"):
        with st.expander(f"补充指令记录 · {len(control['instructions'])}"):
            st.dataframe(
                [{"序号": row.get("seq"), "指令": row.get("text"),
                  "状态": "已注入" if row.get("consumed_at") else "等待注入"}
                 for row in control["instructions"]],
                hide_index=True, width="stretch",
            )

    for index, step in enumerate(trace.get("steps", []), start=1):
        step_phase = phase_labels.get(step.get("phase"), step.get("phase") or "阶段未知")
        with st.expander(f"第 {index} 轮 · {step_phase} · {step.get('finish_reason') or '进行中'}"):
            if output_mode:
                st.caption(f"本轮沿用任务冻结的输出设置：{output_description}")
            if step.get("plan"):
                st.write(f"**本轮模型说明：** {step['plan']}")
            st.caption(
                f"请求状态：{request_labels.get(step.get('request_state'), step.get('request_state') or '未记录')} · "
                f"耗时：{step.get('duration_seconds') if step.get('duration_seconds') is not None else '—'} 秒 · "
                f"原始响应：{'已记录' if step.get('raw_available') else '无'}"
            )
            if step.get("tools"):
                st.markdown("**工具动作与结果**")
                for tool_index, tool in enumerate(step["tools"], start=1):
                    st.write(f"{tool_index}. **{tool_labels.get(tool.get('name'), tool.get('name') or '工具动作')}**")
                    if tool.get("args"):
                        st.json(tool["args"], expanded=False)
                    if tool.get("result"):
                        st.write(tool["result"].get("summary", "已返回结果"))
                        if tool["result"].get("preview"):
                            st.caption(tool["result"]["preview"])
                        if tool["result"].get("matches"):
                            st.dataframe(tool["result"]["matches"], hide_index=True, width="stretch")
                        if tool["result"].get("diagnostics"):
                            st.markdown("**旧提取诊断**")
                            st.dataframe(tool["result"]["diagnostics"], hide_index=True, width="stretch")
                        if tool["result"].get("entity_types"):
                            st.caption(
                                "旧提取记录的实体类型（按原样显示）："
                                + "、".join(tool["result"]["entity_types"])
                            )
                        if tool["result"].get("error"):
                            st.warning(tool["result"]["error"])
            if step.get("staged") or step.get("removed"):
                st.write("**本轮草稿变化**")
                for item in step.get("staged", []):
                    st.write(f"新增／更新：{item.get('label', '未命名对象')}")
                for item in step.get("removed", []):
                    st.write(f"移除：{item.get('label', '未命名对象')}")
            if step.get("diagnostics"):
                st.markdown("**本轮校验与预检诊断**")
                st.dataframe(step["diagnostics"], hide_index=True, width="stretch")
            if step.get("candidate_label"):
                st.success(f"已形成待审核候选：{step['candidate_label']}")
            with st.expander("技术详情 · 轮次原始数据"):
                st.json({
                    key: value for key, value in step.items()
                    if key not in {"tools", "plan"}
                }, expanded=False)
                if step.get("raw_available"):
                    response_key = f"agent-response-visible-{run_id}-{step.get('round', index)}"
                    if st.button("查看本轮模型返回正文", key=f"agent-response-button-{run_id}-{index}"):
                        st.session_state[response_key] = True
                    if st.session_state.get(response_key):
                        response = operations.narrative_analysis_response(
                            run_id, int(step.get("round") or index)
                        )
                        st.caption(
                            f"{response.get('phase', '阶段未记录')} · "
                            f"{response.get('finish_reason', '终止原因未记录')} · "
                            f"{response.get('prompt_version', '提示版本未记录')}"
                        )
                        st.code(response.get("raw") or "本轮没有返回正文", language="json")
    if trace.get("error"):
        st.error(trace["error"])


def harness_settings_panel(operations: Operations):
    """One shared provider/model profile for both agent and one-shot extraction."""
    config = operations.harness_settings()
    provider_options = operations.provider_catalog()
    provider_names = {item["id"]: item["name"] for item in provider_options}
    provider_key = f"provider-selection-{operations.project_info().path}"
    if provider_key not in st.session_state:
        st.session_state[provider_key] = config["provider"]
    selected_provider = st.selectbox(
        "模型供应商",
        list(provider_names),
        format_func=lambda key: provider_names[key],
        key=provider_key,
    )
    assert selected_provider is not None
    profile = operations.harness_settings(selected_provider)
    info = next(item for item in provider_options if item["id"] == selected_provider)
    st.caption(info["notes"])
    if info["docs_url"]:
        st.markdown(f"[供应商官方接口说明]({info['docs_url']})")
    catalog = model_options(selected_provider)
    model_input_key = f"harness-model-input-{operations.project_info().path}-{selected_provider}"
    model_drafts_key = f"harness-model-drafts-{operations.project_info().path}"
    model_drafts = st.session_state.get(model_drafts_key, {})
    if model_input_key not in st.session_state:
        st.session_state[model_input_key] = model_drafts.get(selected_provider, profile["model"])
    if catalog:
        catalog_key = f"harness-model-catalog-{operations.project_info().path}-{selected_provider}"
        catalog_last_key = f"{catalog_key}-last"
        st.session_state.setdefault(catalog_last_key, None)
        if catalog_key not in st.session_state:
            st.session_state[catalog_key] = st.session_state[catalog_last_key]
        selected_model = st.selectbox(
            "内置模型建议（可选）",
            [None, *[item["id"] for item in catalog]],
            format_func=lambda model_id: (
                f"保留当前／手动填写：{profile['model'] or '未设置'}"
                if model_id is None
                else next(
                    f"{item['name']} · {item['id']}" for item in catalog if item["id"] == model_id
                )
            ),
            key=catalog_key,
        )
        if selected_model != st.session_state[catalog_last_key]:
            model_value = selected_model or profile["model"]
            st.session_state[model_input_key] = model_value
            model_drafts = dict(st.session_state.get(model_drafts_key, {}))
            model_drafts[selected_provider] = model_value
            st.session_state[model_drafts_key] = model_drafts
            st.session_state[catalog_last_key] = selected_model
        source_url = MODEL_CATALOG_SOURCES.get(selected_provider)
        if source_url:
            st.caption(
                "建议列表于 2026-10-18 按官方资料核对；账号权限和套餐可用性以供应商为准。"
            )
            st.markdown(f"[模型目录来源]({source_url})")
        if selected_provider == "ollama":
            st.caption("本地模型需先在 Ollama 中安装；也可以直接填写其他本地模型名。")
    elif selected_provider == "ark-standard":
        st.caption("通用方舟服务使用控制台创建的推理接入点 ID；不提供通用模型名列表。")
        st.markdown(f"[官方说明]({MODEL_CATALOG_SOURCES['ark-standard']})")
    st.caption("这是 agent 与快捷提取共用的服务设置；每家服务分别保存。")
    profile_key = f"harness-profile-{operations.project_info().path}-{selected_provider}"
    st.session_state.setdefault(f"{profile_key}-base-url", profile["base_url"])
    st.session_state.setdefault(f"{profile_key}-effort", profile["reasoning_effort"] or "")
    st.session_state.setdefault(f"{profile_key}-timeout", profile["timeout_seconds"])
    token_draft_key = f"{profile_key}-tokens-draft"
    st.session_state.setdefault(token_draft_key, profile["max_tokens"] or 131072)
    token_widget_key = f"{profile_key}-tokens-input"
    token_mode_key = f"{profile_key}-token-mode"
    st.session_state.setdefault(
        token_mode_key,
        "service-default" if profile["max_tokens"] is None else "explicit",
    )
    st.session_state.setdefault(f"{profile_key}-key-env", profile["key_env"])
    st.session_state.setdefault(f"{profile_key}-token-param", profile["token_parameter"])
    address = st.text_input("API 地址", key=f"{profile_key}-base-url")
    model = st.text_input(
        "精确模型 ID", placeholder="填写服务控制台中可用的模型名称或接入点 ID",
        key=model_input_key,
        on_change=remember_model_draft,
        args=(selected_provider, model_input_key, model_drafts_key),
    )
    model_drafts = dict(st.session_state.get(model_drafts_key, {}))
    model_drafts[selected_provider] = model
    st.session_state[model_drafts_key] = model_drafts
    effort = st.text_input("推理强度（留空不发送）", key=f"{profile_key}-effort")
    timeout = st.number_input(
        "每块超时（秒）", min_value=5, max_value=600, key=f"{profile_key}-timeout"
    )
    token_mode = st.selectbox(
        "服务输出上限",
        ["explicit", "service-default"],
        format_func=lambda mode: {
            "explicit": "指定单次输出上限",
            "service-default": "使用服务默认（不发送上限参数）",
        }[mode],
        key=token_mode_key,
        help="服务默认由所选服务决定，不代表自动使用最大值。",
    )
    tokens = None
    if token_mode == "explicit":
        if token_widget_key not in st.session_state:
            st.session_state[token_widget_key] = st.session_state[token_draft_key]
        tokens = int(st.number_input(
            "每块最大输出 Token", min_value=1, step=1, key=token_widget_key,
            on_change=remember_number_draft, args=(token_widget_key, token_draft_key),
            help="不设本地最大值；服务是否支持该数值由所选模型和接口决定。",
        ))
    else:
        st.caption("请求中不会发送输出上限字段；由服务按自身默认处理。")
    context_settings = {}
    for field, label, default in (
        ("context_window", "上下文窗口（token）", 1_000_000),
        ("output_reserve", "输出预留（token）", 131_072),
        ("compaction_reserve", "压缩预留（token）", 65_536),
        ("compaction_max_tokens", "压缩最大输出（token）", 32_768),
    ):
        widget_key = f"{profile_key}-{field}"
        st.session_state.setdefault(widget_key, int(profile.get(field, default)))
        context_settings[field] = int(st.number_input(
            label, min_value=0 if field in {"output_reserve", "compaction_reserve"} else 1,
            step=1, key=widget_key,
        ))
    history_budget = (
        context_settings["context_window"] - max(context_settings["output_reserve"], tokens or 0)
        - context_settings["compaction_reserve"]
    )
    st.caption(f"可用历史阈值：{max(0, history_budget):,} token。单次生成上限仍独立设置。")
    st.caption("有效输出预留取输出预留与单次生成上限的较大值。")
    if history_budget <= 0:
        st.error("上下文窗口须大于有效输出预留与压缩预留之和。")
    with st.expander("高级适配设置"):
        key_env = st.text_input(
            "密钥环境变量名称", key=f"{profile_key}-key-env",
            help="只填环境变量名称，不要在此填写密钥。",
        )
        token_parameter = st.selectbox(
            "输出上限字段", ["max_tokens", "max_completion_tokens"],
            key=f"{profile_key}-token-param",
        )
        st.caption(
            "max_completion_tokens 在部分接口中会计入推理 token；具体含义由所选接口决定。"
        )
    if st.button("保存服务设置", disabled=history_budget <= 0):
        if tokens is not None:
            st.session_state[token_draft_key] = tokens
        try:
            operations.save_harness_settings({
                "provider": selected_provider, "base_url": address, "model": model,
                "reasoning_effort": effort or None, "timeout_seconds": timeout,
                "max_tokens": tokens, "key_env": key_env, "token_parameter": token_parameter,
                **context_settings,
            })
            st.rerun()
        except (OperationError, ValueError) as exc:
            st.error(str(exc))
    return selected_provider, selected_provider != config["provider"]


def evidence_view(operations: Operations, evidence):
    try:
        excerpt = operations.evidence_excerpt(evidence)
    except OperationError as exc:
        st.error(str(exc))
        return
    st.caption(f"{excerpt.title} · 字符 {excerpt.start}–{excerpt.end}")
    markup = (
        html.escape(excerpt.before)
        + "<mark>"
        + html.escape(excerpt.quote)
        + "</mark>"
        + html.escape(excerpt.after)
    )
    st.markdown(
        f'<div style="white-space:pre-wrap;line-height:1.9;padding:16px;border:1px solid #ddd;border-radius:8px">{markup}</div>',
        unsafe_allow_html=True,
    )
    with st.expander("技术详情 · 原文指纹"):
        st.code(excerpt.source_hash)


def evidence_picker(operations: Operations, key: str, caption: str):
    sources = operations.sources()
    if not sources:
        st.info("请先导入原文。")
        return None
    source_id = st.selectbox(
        "证据原文",
        [source["id"] for source in sources],
        format_func=lambda ref: next(source["title"] for source in sources if source["id"] == ref),
        key=f"{key}-source",
    )
    assert source_id is not None
    quote = st.text_area(caption, key=f"{key}-quote")
    matches = operations.locate(source_id, quote) if quote.strip() else []
    if not matches:
        st.caption("输入原文中的精确片段后选择证据位置。")
        return None
    index = st.selectbox(
        "证据位置",
        list(range(len(matches))),
        format_func=lambda position: (
            f"字符 {matches[position].start}–{matches[position].end}"
        ),
        key=f"{key}-position",
    )
    selected = matches[index]
    evidence_view(operations, selected)
    return selected


def source_tab(operations: Operations):
    source_key = f"selected-source-{operations.project_info().path}"
    catalog = operations.builtin_text_catalog()
    with st.expander(f"内置真实文本（{len(catalog)} 篇）", expanded=not operations.sources()):
        st.caption("公版原文可离线载入。全文与节选分别标明；载入后可到「提取交接」启动模型提取。")
        text_id = st.selectbox(
            "选择内置文章",
            [entry["id"] for entry in catalog],
            format_func=lambda key: next(
                f"《{entry['title']}》 · {entry['author']} · {entry['extent']}"
                for entry in catalog
                if entry["id"] == key
            ),
        )
        if text_id is not None:
            article = operations.builtin_text(text_id)
            st.write(article["description"])
            st.caption(
                f"{article['characters']} 字符 · {article['selection']} · {article['rights']}"
            )
            st.markdown(f"[原文出处：维基文库]({article['url']})")
            with st.expander("预览内置原文"):
                st.text_area("内置原文预览", article["text"], height=250, disabled=True)
            if st.button("载入所选原文", type="primary"):
                st.session_state[source_key] = operations.import_builtin_text(text_id)
                st.session_state["builtin-text-notice"] = (
                    f"已载入《{article['title']}》{article['extent']}。重复载入不会新增副本。"
                )
                st.rerun()
    if notice := st.session_state.pop("builtin-text-notice", None):
        st.success(notice)
    with st.expander("导入章节", expanded=not operations.sources()):
        with st.form("import"):
            title = st.text_input("章节标题")
            upload = st.file_uploader("选择 UTF-8 文本", type=["txt", "md"])
            pasted = st.text_area("或粘贴原文", height=140)
            if st.form_submit_button("保存原文", type="primary"):
                if upload:
                    operations.import_bytes(title, upload.getvalue())
                else:
                    operations.import_text(title, pasted)
                st.rerun()
    sources = operations.sources()
    if not sources:
        st.info("可从上方选择内置真实文本，也可自行导入原文或在左侧载入自编演示。")
        return
    source_id = st.selectbox(
        "章节",
        [s["id"] for s in sources],
        format_func=lambda i: next(s["title"] for s in sources if s["id"] == i),
        key=source_key,
    )
    assert source_id is not None
    source = operations.source(source_id)
    attribution = operations.source_attribution(source_id)
    if attribution:
        st.caption(
            f"{attribution['author']} · {attribution['extent']} · {attribution['selection']}"
        )
        st.markdown(f"[原文出处：维基文库]({attribution['url']})")
    st.caption(f"固定原文 · {len(source['text'])} 字符 · 不改写换行或字形")
    with st.expander("技术详情 · 原文指纹"):
        st.code(source["hash"])
    quote = st.text_input("查找原句（精确匹配）")
    if quote:
        matches = operations.locate(source_id, quote)
        st.caption(f"找到 {len(matches)} 个位置；相同句子分别保留。")
        for ev in matches:
            evidence_view(operations, ev)
    else:
        st.text_area("章节原文", source["text"], height=450, disabled=True)


def entity_tab(operations: Operations):
    entities = operations.entities()
    st.caption("实体 ID 独立于姓名和别名；不自动推定存活、所在地或关系状态。")
    if entities:
        st.dataframe(
            [
                {
                    "姓名": e["name"],
                    "类型": e["entity_type"],
                    "别名": "、".join(e["aliases"]),
                }
                for e in entities
            ],
            hide_index=True,
            width="stretch",
        )
        with st.expander("技术详情 · 实体 ID"):
            st.dataframe([{"姓名": e["name"], "ID": e["id"]} for e in entities], hide_index=True, width="stretch")
    options = [None] + [e["id"] for e in entities]
    selected = st.selectbox(
        "登记或纠正实体",
        options,
        format_func=lambda i: (
            "新增实体" if i is None else next(e["name"] for e in entities if e["id"] == i)
        ),
    )
    current = next((e for e in entities if e["id"] == selected), None)
    with st.form(f"entity-{selected}"):
        name = st.text_input("姓名／名称", value=current["name"] if current else "")
        types: list[Literal["person", "location", "item", "other"]] = [
            "person",
            "location",
            "item",
            "other",
        ]
        kind = st.selectbox(
            "类型",
            types,
            index=types.index(current["entity_type"]) if current else 0,
            format_func=lambda t: {
                "person": "人物",
                "location": "地点",
                "item": "物品",
                "other": "其他",
            }[t],
        )
        aliases = st.text_input(
            "别名（用逗号分隔）", value=",".join(current["aliases"]) if current else ""
        )
        if st.form_submit_button("提交为待审核候选"):
            assert kind is not None
            operations.propose_entity(
                entity_id=selected,
                name=name,
                entity_type=kind,
                aliases=[a.strip() for a in re.split("[,，]", aliases) if a.strip()],
            )
            st.success("已创建候选，请到「候选审核」查看差异并接受。")


def assertion_tab(operations: Operations):
    entities = operations.entities()
    names = {e["id"]: e["name"] for e in entities}
    assertions = operations.assertions()
    if assertions:
        scope_filter = st.selectbox(
            "显示范围", ["all", *LABELS], format_func=lambda x: "全部" if x == "all" else LABELS[x]
        )
        for assertion in assertions:
            if scope_filter != "all" and assertion.scope != scope_filter:
                continue
            title = f"{names.get(assertion.subject_id, '?')} · {assertion.predicate} = {assertion.value} · {LABELS[assertion.scope]}"
            with st.expander(title):
                st.caption(
                    f"{assertion.status} · 来源 {assertion.origin} · 时间 {assertion.story_time or '未解析'} · 确定程度 {assertion.certainty}"
                )
                if assertion.holder_id:
                    st.warning(
                        f"所属人物：{names.get(assertion.holder_id)}；这条记录属于人物的说法／认知。"
                    )
                for ev in assertion.evidence:
                    evidence_view(operations, ev)
                st.json(assertion.model_dump(), expanded=False)
                if st.button("创建纠正候选", key=f"correct-{assertion.id}"):
                    operations.propose_correction(assertion.id)
                    st.success("已创建，可在候选审核里编辑。")
    if not entities or not operations.sources():
        st.info("新建断言需要原文和至少一个已接受实体。")
        return
    st.divider()
    st.subheader("建立有证据的断言")
    subject = st.selectbox("关于谁／什么", list(names), format_func=names.__getitem__)
    assert subject is not None
    predicate = st.text_input("属性／断言名称", placeholder="例如：持有、所在地、状态")
    value_type = st.selectbox("值的类型", ["文字", "未知", "是", "否", "数字"])
    value_text = st.text_input("值", disabled=value_type in {"未知", "是", "否"})
    scope_options: list[Literal["world", "claim", "knowledge"]] = ["world", "claim", "knowledge"]
    scope = st.selectbox("这是谁的事实或说法", scope_options, format_func=LABELS.__getitem__)
    assert scope is not None
    people = {e["id"]: e["name"] for e in entities if e["entity_type"] == "person"}
    holder = st.selectbox(
        "主张／认知所属人物",
        [None, *people],
        format_func=lambda x: "未指定" if x is None else people[x],
        disabled=scope == "world",
    )
    learning = st.text_input("获得信息的方式（认知记录可填）", placeholder="例如：目击、被告知")
    time = st.text_input("故事有效时间（可留空）", placeholder="例如：雨夜交接后；不是章节编号")
    sources = operations.sources()
    source_id = st.selectbox(
        "证据章节",
        [s["id"] for s in sources],
        format_func=lambda i: next(s["title"] for s in sources if s["id"] == i),
        key="assertion-source",
    )
    assert source_id is not None
    quote = st.text_area("粘贴精确原句", height=90)
    matches = operations.locate(source_id, quote)
    ev_index = st.selectbox(
        "证据位置（重复原句须选定）",
        [None, *range(len(matches))],
        format_func=lambda i: (
            "请选择位置" if i is None else f"字符 {matches[i].start}–{matches[i].end}"
        ),
    )
    if ev_index is not None:
        evidence_view(operations, matches[ev_index])
    if st.button("提交断言候选", type="primary", disabled=ev_index is None):
        assert ev_index is not None
        value = {"未知": None, "是": True, "否": False}.get(value_type, value_text)
        if value_type == "数字":
            value = float(value_text)
        operations.propose_assertion(
            subject_id=subject,
            predicate=predicate.strip(),
            value=value,
            scope=scope,
            holder_id=holder if scope != "world" else None,
            learning_method=learning.strip() or None,
            story_time=time.strip() or None,
            evidence=matches[ev_index],
        )
        st.success("已创建断言候选，等待审核。")


def review_tab(operations: Operations):
    pending = operations.candidates(status="pending")
    if not pending:
        st.info("没有待审核候选。登记实体、建立断言或导入提取结果后会出现在这里。")
        return
    display_map = operations.analysis_display_map()
    change_id = st.selectbox(
        "待审核候选",
        [r["id"] for r in pending],
        format_func=lambda i: candidate_label(operations, i, display_map),
    )
    assert change_id is not None
    review = operations.review_candidate(change_id)
    change = review.change
    preview = operations.candidate_preview(change_id)
    if preview["version"] != review.version:
        st.rerun()
    if notice := st.session_state.pop("review-notice", None):
        st.success(notice)
    st.caption(
        f"候选基于修订 {change.expected_revision} · 当前修订 {review.current_revision} · 候选版本 {review.version}"
    )
    if change.note:
        st.write(change.note)
    counts = {
        kind: sum(op.kind == kind for op in change.operations)
        for kind in ("entity", "assertion", "event")
    }
    st.caption(
        f"本批候选：{counts['entity']} 个实体、{counts['assertion']} 条断言、{counts['event']} 个事件。"
    )
    additional = len(change.operations) - sum(counts.values())
    if additional:
        st.caption(f"另有 {additional} 条状态、认知、关系或其它资料，按下方差异逐项审核。")
    st.caption(preview["projection_note"])
    kept_ids = []
    review_project = operations.project_info().path
    for index, card in enumerate(preview["cards"]):
        with st.expander(
            f"{card['kind_label']} · {card['title']}",
            expanded=bool(
                card["state_writes"]
                or card["knowledge_writes"]
                or any(item["severity"] == "error" for item in card["diagnostics"])
            ),
        ):
            if st.checkbox(
                "保留本条",
                value=True,
                key=f"keep-{review_project}-{change.id}-{review.version}-{index}",
            ):
                kept_ids.append(card["operation_id"])
            if card["ownership"]:
                st.write(card["ownership"])
            for line in card["summary"]:
                st.write(line)
            for effect in card["state_writes"] + card["knowledge_writes"]:
                st.write(f"**拟产生的变化：** {effect['summary']}")
            for warning in card["warnings"]:
                st.warning(warning["message"])
            for evidence in change.operations[index].evidence:
                evidence_view(operations, evidence)
            if card["dependencies"]:
                st.caption("本条依赖其它对象；排除被引用条目后，校验会阻止采用。")
    selection_changed = len(kept_ids) != len(change.operations)
    if selection_changed:
        st.info("逐条选择尚未保存。保存后会重新校验引用；被排除条目保留在拒绝记录中。")
    if st.button("保存逐条审核结果", disabled=not selection_changed):
        selection = operations.select_candidate_operations(change.id, kept_ids, review.version)
        st.session_state["review-notice"] = (
            f"已保留 {selection['kept_count']} 条、拒绝 {selection['rejected_count']} 条；请核对更新后的校验。"
        )
        st.rerun()
    with st.expander("变更前后（原始字段）"):
        for diff in review.differences:
            left, right = st.columns(2)
            with left:
                st.caption("当前正式资料")
                st.json(diff["before"] or {"提示": "新增对象"}, expanded=False)
            with right:
                st.caption("候选内容")
                st.json(diff["after"], expanded=False)
    diagnostics = review.diagnostics
    st.dataframe(
        [
            {"规则": d.rule_id, "判定": d.verdict, "级别": d.severity, "说明": d.message}
            for d in diagnostics
        ],
        hide_index=True,
        width="stretch",
    )
    if change.expected_revision != review.current_revision:
        st.warning("资料已经改变。请检查上方最新差异，再更新候选基准并重新审核。")
        if st.button("已查看最新差异，更新基准修订"):
            operations.rebase_candidate(change.id, review.version, review.current_revision)
            st.rerun()
    with st.expander("编辑候选（结构化 JSON）"):
        with st.form(f"edit-{change.id}-{review.version}"):
            edited = st.text_area(
                "候选内容",
                json.dumps(change.model_dump(), ensure_ascii=False, indent=2),
                height=330,
            )
            if st.form_submit_button("保存候选修改"):
                operations.edit_candidate(change.id, edited, review.version)
                st.rerun()
    accept, reject = st.columns(2)
    if accept.button(
        "接受并保存正式资料",
        type="primary",
        disabled=selection_changed or any(d.severity == "error" for d in diagnostics),
    ):
        operations.accept_candidate(change.id, review.version)
        st.rerun()
    if reject.button("拒绝候选"):
        operations.reject_candidate(change.id, review.version)
        st.rerun()


def extraction_diagnostics_view(preview: dict):
    diagnostics = preview.get("diagnostics", [])
    if not diagnostics:
        return
    st.write(f"**提取结果检查：发现 {len(diagnostics)} 项问题**")
    st.caption("下表同时列出已能定位的问题；修正格式后仍须重新检查，核验故事含义。")
    st.dataframe(
        [
            {
                "字段位置": item["path"],
                "类型": {
                    "structure": "格式",
                    "reference": "引用",
                    "time": "时间",
                    "evidence": "证据",
                    "type": "对象或值类型",
                    "contract": "动作约束",
                }.get(item["category"], item["category"]),
                "说明": item["message"],
                "修正建议": item.get("suggestion", "请对照原文人工修正"),
                "原句": item.get("quote", ""),
            }
            for item in diagnostics
        ],
        hide_index=True,
        width="stretch",
    )


def token_count(summary: dict, field: str) -> str:
    value = summary.get(field)
    if value is None:
        return "未知"
    return ("≥" if field in summary.get("partial_fields", []) else "") + f"{value:,}"


def token_usage_view(summary: dict, scope: str):
    for column, field, label in zip(
        st.columns(3),
        ("input_tokens", "output_tokens", "total_tokens"),
        ("输入", "输出", "总"),
        strict=True,
    ):
        column.metric(f"{scope}{label} Token", token_count(summary, field))
    st.caption(
        f"已取得用量：{summary['reported_chunks']}/{summary['counted_chunks']} 个执行／导入分块；"
        f"其中 {summary['incomplete_chunks']} 个分块用量不完整。≥ 表示已知累计，未知部分不按 0 计。"
    )
    st.caption(
        "按保存的服务用量汇总，含失败结果；缓存属于输入、推理属于输出，不重复加到总量。不是费用账单。"
    )
    if (
        summary.get("cached_input_tokens") is not None
        or summary.get("reasoning_tokens") is not None
    ):
        st.caption(
            f"其中缓存输入：{token_count(summary, 'cached_input_tokens')} Token · "
            f"推理输出：{token_count(summary, 'reasoning_tokens')} Token"
        )


def project_token_usage(operations: Operations):
    with st.expander("项目 Token 统计"):
        summary = operations.token_usage()
        token_usage_view(summary, "项目")
        if summary["by_model"]:
            names = {item["id"]: item["name"] for item in operations.provider_catalog()}
            st.dataframe(
                [
                    {
                        "供应商": names.get(row["provider"], "未知"),
                        "模型": row["model"] if row["model"] != "unknown" else "未知",
                        "输入 Token": token_count(row, "input_tokens"),
                        "输出 Token": token_count(row, "output_tokens"),
                        "总 Token": token_count(row, "total_tokens"),
                        "有用量／执行分块": f"{row['reported_chunks']}/{row['counted_chunks']}",
                    }
                    for row in summary["by_model"]
                ],
                hide_index=True,
                width="stretch",
            )


@st.fragment(run_every="2s")
def extraction_status(operations: Operations, run_id: str):
    run = next(r for r in operations.runs() if r["id"] == run_id)
    chunks = operations.chunks(run_id)
    diagnostics = operations.extraction_run_diagnostics(run_id)
    incomplete = diagnostics["finish_reasons"]
    manually_repaired = diagnostics["manually_repaired"]
    labels = {
        "awaiting_results": "等待启动／手工导入",
        "queued": "等待执行",
        "running": "正在提取",
        "complete": "提取结果已解析",
        "partial": "部分结果已导入",
        "failed": "提取失败",
        "cancelled": "已停止",
        "interrupted": "上次进程中断",
    }
    status_label = labels.get(run["status"]) or str(run["status"])
    if manually_repaired and run["status"] in {"complete", "partial"}:
        status_label += "（含人工修正）"
    st.write(f"**任务状态：{status_label}**")
    if run["data"].get("error"):
        st.error(run["data"]["error"])
    if incomplete:
        st.warning(
            (
                "模型原响应因输出容量截断"
                if diagnostics["capacity_truncated"]
                else "模型原响应未完整结束"
            )
            + f"（finish_reason={', '.join(incomplete)}）。"
            + (
                "当前候选由人工修正完整结果后导入，仍需审核；原响应及用量已保留。"
                if manually_repaired
                else "该响应未自动导入；原响应及用量已保留。可调整分块或输出上限，新建任务重新提取。"
            )
        )
    if run["status"] in {"queued", "running"} and st.button("停止提取"):
        operations.cancel_extraction(run_id)
        st.rerun(scope="fragment")
    summary = operations.token_usage(run_id)
    token_usage_view(summary, "任务")
    project_token_usage(operations)
    usage_by_chunk = {row["chunk_id"]: row for row in summary["chunks"]}
    st.dataframe(
        [
            {
                "分块": c["id"],
                "起点": c["start"],
                "终点": c["end"],
                "状态": c["status"],
                "输入 Token": token_count(usage_by_chunk.get(c["id"], {}), "input_tokens"),
                "输出 Token": token_count(usage_by_chunk.get(c["id"], {}), "output_tokens"),
                "总 Token": token_count(usage_by_chunk.get(c["id"], {}), "total_tokens"),
                "错误": (c["error"] or "").splitlines()[0] if c["error"] else "",
            }
            for c in chunks
        ],
        hide_index=True,
        width="stretch",
    )
    if run["status"] not in {"awaiting_results", "queued", "running"} and st.button(
        "刷新页面以审核候选"
    ):
        st.rerun()


def recovery_panel(operations: Operations, chunk_id: str):
    saved = operations.extraction_review(chunk_id)
    if not saved["can_repair"] or not saved["raw"]:
        return
    with st.expander("修正证据后重新导入", expanded=saved["status"] == "failed"):
        st.caption("直接修正保存的结果，不调用模型。原始响应和每次修正均会保留；导入后仍须审核。")
        if saved.get("finish_reason"):
            st.warning(
                (
                    "模型原响应因输出容量截断"
                    if saved.get("capacity_truncated")
                    else "模型原响应未完整结束"
                )
                + f"（finish_reason={saved['finish_reason']}）。原样不能导入；"
                "若手工补全，将作为人工修正记录，不能视为模型完整提取。"
            )
        if saved["error"]:
            st.warning("该分块尚未成功导入，请按下方检查表修正。原始结果已保留。")
            with st.expander("查看上次失败详情"):
                st.code(saved["error"], language=None)
        with st.expander("对照本分块原文"):
            st.text_area(
                "本分块原文",
                saved["text"],
                height=220,
                disabled=True,
                key=f"repair-source-{chunk_id}",
            )
        version = f"{chunk_id}-{hash(saved['raw'])}"
        with st.expander("修改完整结果 JSON（格式或字段错误时使用）"):
            raw = st.text_area(
                "待修正的提取结果 JSON",
                saved["raw"],
                height=260,
                key=f"repair-json-{version}",
            )
        preview = operations.extraction_review(chunk_id, raw)
        extraction_diagnostics_view(preview)
        if preview["parse_error"]:
            st.info("结果格式尚未通过校验，请展开上方完整结果修正。")
            with st.expander("查看格式校验详情"):
                st.code(preview["parse_error"], language=None)
            return
        quotes: dict[int, str] = {}
        starts: dict[int, int] = {}
        event_quotes: dict[int, str] = {}
        event_starts: dict[int, int] = {}
        unresolved = any(
            item.get("blocks_import") and item["category"] != "evidence"
            for item in preview.get("diagnostics", [])
        )
        issues = {"missing": "未找到原句", "ambiguous": "有多个位置", "offset": "位置不匹配"}
        for item in preview.get("entries", preview["assertions"]):
            if item.get("invalid"):
                unresolved = True
                continue
            index = item["index"]
            number = index + 1
            if item.get("kind", "assertion") not in {"assertion", "event"}:
                kind_label = {"state": "状态", "relation": "关系", "knowledge": "认知"}.get(
                    item["kind"], item["kind"]
                )
                with st.expander(f"{kind_label}第 {number} 条", expanded=item["issue"] is not None):
                    st.write(item["quote"])
                    if item["issue"]:
                        unresolved = True
                        st.warning(issues[item["issue"]])
                        st.caption(
                            "在上方完整结果中修改本条 quote 或 quote_start；下表位置相对于当前分块。"
                        )
                        st.dataframe(item["matches"], hide_index=True, width="stretch")
                continue
            is_event = item.get("kind") == "event"
            prefix = "事件" if is_event else ""
            title = f"{prefix}第 {number} 条 · {item['title'] if is_event else item['predicate']}"
            if item["issue"]:
                title += f" · {issues[item['issue']]}"
            with st.expander(title, expanded=item["issue"] is not None):
                quote = (
                    st.text_area(
                        f"{prefix}引文 · 第 {number} 条",
                        item["quote"],
                        height=80,
                        key=f"repair-quote-{prefix}-{version}-{hash(raw)}-{index}",
                    )
                    or ""
                )
                if quote != item["quote"]:
                    if is_event:
                        event_quotes[index] = quote
                        updated = operations.extraction_review(
                            chunk_id, raw, event_quotes={index: quote}
                        )
                        item = updated["events"][index]
                    else:
                        quotes[index] = quote
                        updated = operations.extraction_review(chunk_id, raw, {index: quote})
                        item = updated["assertions"][index]
                matches = {match["quote_start"]: match for match in item["matches"]}
                if not matches:
                    st.warning("原文中没有连续匹配的引文，请对照原文修正。")
                    unresolved = True
                    continue
                options = [None, *matches]
                current = item["quote_start"]
                selected = st.selectbox(
                    f"{prefix}证据位置 · 第 {number} 条",
                    options,
                    index=options.index(current) if current in options else 0,
                    format_func=lambda pos, matches=matches: (
                        "请选择原文位置"
                        if pos is None
                        else f"字符 {matches[pos]['start']}–{matches[pos]['end']} · "
                        f"{matches[pos]['before']}【{matches[pos]['quote']}】{matches[pos]['after']}"
                    ),
                    key=f"repair-position-{prefix}-{version}-{hash(raw)}-{index}-{hash(quote)}",
                )
                if selected is not None:
                    if is_event:
                        event_starts[index] = selected
                    else:
                        starts[index] = selected
                    match = matches[selected]
                    st.text(f"{match['before']}【{match['quote']}】{match['after']}")
                elif item["issue"]:
                    unresolved = True
        if unresolved:
            st.info("请修正未匹配的引文，并为重复原句或错误位置选定证据。")
        if st.button(
            "保存修正并导入候选",
            disabled=unresolved or (bool(saved.get("finish_reason")) and raw == saved["raw"] and not quotes and not event_quotes and not starts and not event_starts),
            key=f"repair-save-{version}",
        ):
            edited = operations.extraction_review(chunk_id, raw, quotes, event_quotes)["edited_raw"]
            candidate_id = operations.repair_extraction_result(
                chunk_id, edited, starts, expected_raw=saved["raw"], event_quote_starts=event_starts
            )
            st.session_state["repair-notice"] = (
                "修正已保存并生成候选，请到「候选审核」核验。"
                if candidate_id
                else "修正已保存；空结果不代表没有事实。"
            )
            st.rerun()


def harness_tab(operations: Operations):
    st.caption(
        "DeepSeek Harness 提取实体、断言与事件；结果进入待审候选，采用事件后进入时间轴。刷新页面不会重复发起请求。"
    )
    if notice := st.session_state.pop("repair-notice", None):
        st.success(notice)
    config = operations.harness_settings()
    provider_names = {item["id"]: item["name"] for item in operations.provider_catalog()}
    st.write(f"**当前共用模型服务：{provider_names[config['provider']]} · {config['model']}**")
    st.caption("模型目录与服务设置在左侧‘模型与服务’中维护，agent 和快捷提取共用已保存配置。")
    service_not_saved = False
    credential_scope = (
        operations.project_info().path,
        config["provider"],
        config["base_url"],
        config["key_env"],
        config["provider"],
    )
    if st.session_state.get("harness-key-scope") != credential_scope:
        st.session_state["harness-api-key"] = ""
        st.session_state["harness-key-scope"] = credential_scope
    api_key = st.text_input(
        f"本次 API 密钥（可留空读取 {config['key_env']}）",
        type="password",
        key="harness-api-key",
        disabled=service_not_saved,
    )
    st.caption("密钥仅用于当前服务的本次运行，不写入项目设置或导出包。")
    credential = operations.harness_credential_status()
    if credential["available"]:
        st.caption("已检测到环境变量中的密钥，密码框可以留空。")
    elif not api_key:
        st.info("尚未检测到密钥，请在上方密码框填写。")
    sources = operations.sources()
    if not sources:
        project_token_usage(operations)
        return
    source_id = st.selectbox(
        "待提取章节",
        [s["id"] for s in sources],
        format_func=lambda i: next(s["title"] for s in sources if s["id"] == i),
        key="run-source",
    )
    assert source_id is not None
    if st.button("创建分块任务"):
        operations.create_run(source_id)
        st.rerun()
    runs = operations.runs()
    if not runs:
        project_token_usage(operations)
        return
    display_map = operations.analysis_display_map()
    run_id = st.selectbox(
        "提取任务", [r["id"] for r in runs], index=len(runs) - 1,
        format_func=lambda ref: display_item_label(display_map, ref, "原文提取任务"),
    )
    assert run_id is not None
    pack = operations.export_run(run_id)
    selected_run = next(r for r in runs if r["id"] == run_id)
    run_data = selected_run.get("data", {})
    frozen_settings = run_data.get("settings") or {}
    frozen_provider = frozen_settings.get("provider")
    provider_names = {item["id"]: item["name"] for item in operations.provider_catalog()}
    st.caption(
        f"历史提取任务 · 创建于 {selected_run.get('created_at') or '时间未记录'} · "
        f"提示版本 {run_data.get('prompt_version') or '未记录'} · "
        f"实际服务 {provider_names.get(frozen_provider, frozen_provider or '未记录')} · "
        f"模型 {run_data.get('model') or frozen_settings.get('model') or '未记录'}"
    )
    st.caption(
        f"将发送 {sum(c['end'] - c['start'] for c in pack['chunks'])} 字符（含重叠）、"
        f"{len(pack['existing_entities'])} 个已有实体、{len(pack.get('existing_events', []))} 个已有事件（含证据）和结果格式到 {config['base_url']} · {config['model']}"
    )
    if st.button(
        "启动模型提取",
        type="primary",
        disabled=service_not_saved or selected_run["status"] != "awaiting_results",
    ):
        operations.start_extraction(run_id, api_key=api_key or None)
        st.rerun()
    extraction_status(operations, run_id)
    for chunk_number, chunk in enumerate(operations.chunks(run_id), start=1):
        if not chunk.get("raw") and not chunk.get("error"):
            continue
        with st.expander(f"第 {chunk_number} 块 · 旧提取诊断与原始响应"):
            if chunk.get("error"):
                st.warning(chunk["error"])
            raw_key = f"extraction-raw-visible-{run_id}-{chunk['id']}"
            if st.button("查看此块模型返回正文", key=f"extraction-raw-button-{run_id}-{chunk_number}"):
                st.session_state[raw_key] = True
            if st.session_state.get(raw_key):
                response = operations.extraction_raw_response(run_id, chunk["id"])
                if response.get("finish_reason"):
                    st.caption(f"终止原因：{response['finish_reason']}")
                if response.get("diagnostics"):
                    st.markdown("**解析与证据诊断**")
                    st.dataframe(response["diagnostics"], hide_index=True, width="stretch")
                st.code(response.get("raw") or "没有模型返回正文", language="json")
    if selected_run["status"] in {"failed", "interrupted", "cancelled", "partial"}:
        st.markdown("**旧提取诊断**")
        st.caption("此按钮会用失败任务启动一个新的 agent 运行。原始响应、原任务状态和正式资料不会被改写。")
        handoff_task = st.text_area(
            "交给 agent 的处理目标",
            value="检查旧提取失败原因，按当前资料格式重新检索原文并建立有证据的草稿；保留不确定性，不沿用无效对象类型。",
            key=f"extraction-handoff-task-{run_id}",
        )
        if st.button(
            "交给 agent 继续处理",
            type="primary",
            disabled=not handoff_task.strip() or service_not_saved,
            key=f"extraction-handoff-{run_id}",
        ):
            try:
                new_run_id = operations.start_narrative_from_extraction(
                    run_id, task=handoff_task.strip(), api_key=api_key or None
                )
            except (OperationError, ValueError) as exc:
                st.error(str(exc))
            else:
                agent_run_key = f"narrative-agent-run-{operations.project_info().path}"
                agent_input_key = f"agent-run-id-{operations.project_info().path}"
                st.session_state[agent_run_key] = new_run_id
                st.session_state[f"{agent_input_key}-pending"] = new_run_id
                st.session_state[f"{agent_run_key}-notice"] = "旧提取已交给新的 agent 任务分析；原任务与原始响应保持不变。"
                st.success("已启动新的 agent 任务；请回到「分析工作区 → 叙事 agent 工作区」查看轨迹。")
    if selected_run["status"] in {"failed", "interrupted", "cancelled", "partial"}:
        largest = max(c["end"] - c["start"] for c in operations.chunks(run_id))
        if largest > 1:
            with st.expander("用较小分块新建任务"):
                st.caption(
                    "重新提取该任务的整篇原文，会产生新的模型请求和候选；旧任务、原响应及用量保留。"
                    "也可先在上方服务设置调整最大输出 Token。新任务创建后仍需手动启动。"
                )
                size = st.number_input(
                    "新分块字符数", min_value=1, max_value=largest - 1,
                    value=max(1, largest // 2), key=f"recovery-size-{run_id}"
                )
                overlap = st.number_input(
                    "相邻分块重叠字符数", min_value=0, max_value=max(0, size - 1),
                    value=min(100, size - 1), key=f"recovery-overlap-{run_id}-{size}"
                )
                if st.button("创建新的较小分块任务"):
                    operations.create_recovery_run(run_id, int(size), int(overlap))
                    st.rerun()
    st.download_button(
        "下载 harness 输入包（包含原文）",
        json.dumps(pack, ensure_ascii=False, indent=2),
        file_name="narrative-input.json",
        mime="application/json",
    )
    chunks = operations.chunks(run_id)
    st.caption(
        "processed 仅表示分块结果已解析；不表示事实准确、已接受或没有遗漏。重叠候选保留供审核，不按相似文本自动合并。"
    )
    chunk_id = st.selectbox(
        "导入结果对应分块", [c["id"] for c in chunks],
        format_func=lambda ref: next(
            f"第 {index + 1} 块 · 字符 {chunk['start']}–{chunk['end']}"
            for index, chunk in enumerate(chunks) if chunk["id"] == ref
        ),
    )
    assert chunk_id is not None
    recovery_panel(operations, chunk_id)
    with st.form("ingest"):
        raw = st.text_area(
            "结构化提取结果 JSON", height=200, placeholder='{"entities": [], "assertions": []}'
        )
        if st.form_submit_button("校验并导入候选"):
            extraction_diagnostics_view(operations.extraction_review(chunk_id, raw))
            try:
                change_id = operations.ingest_result(chunk_id, raw)
            except OperationError as exc:
                st.error(str(exc))
            else:
                st.success("已导入候选，请审核。" if change_id else "已记录空结果；不代表该分块没有事实。")
    with st.expander("查看保存的原始结果"):
        selected = next(c for c in chunks if c["id"] == chunk_id)
        st.code(selected["raw"] or "尚无结果", language="json")


def time_query_panel(operations: Operations, names: dict[str, str]):
    request_key = f"time-query-request-{operations.project_info().path}"
    display_map = operations.analysis_display_map()
    with st.expander("按故事时刻查询", expanded=request_key in st.session_state):
        st.caption("查询已采用的人物位置、物品持有者、穿戴、伤势、属性、关系与认知；不调用模型。")
        st.caption("发生时刻未定的事件可能落在查询时刻前后；这类影响会保持未知，并列出对应原句。")
        with st.form(f"time-query-form-{operations.project_info().path}"):
            minute = st.number_input("查询时刻（相对分钟）", value=0.0, step=1.0)
            boundary_label = st.radio(
                "同刻事件边界", ["包含同刻结束的事件", "不包含同刻结束的事件"], horizontal=True
            )
            if st.form_submit_button("查询此刻状态"):
                st.session_state[request_key] = {
                    "minute": minute,
                    "boundary": "after" if boundary_label == "包含同刻结束的事件" else "before",
                }
        request = st.session_state.get(request_key)
        if request is None:
            return
        result = operations.state_at_time(request["minute"], request["boundary"])
        boundary_text = (
            "包含同刻结束的事件" if result["boundary"] == "after" else "不包含同刻结束的事件"
        )
        st.write(f"**第 {result['minute']:g} 分钟 · {boundary_text}**")
        st.caption(f"查询修订 {result['revision']} · 结果随正式修订重算；未知原因逐项列明。")
        for field, title in (
            ("holders", "物品持有者（时刻查询）"),
            ("locations", "人物所在地（时刻查询）"),
        ):
            st.write(f"**{title}**")
            rows = [
                {
                    "对象": display_item_label(display_map, ref, names.get(ref, "未知对象")),
                    "此刻状态": display_item_label(display_map, value, "未知"),
                    "依据与未知原因": result["reasons"][field].get(ref, "尚无确定依据"),
                }
                for ref, value in result[field].items()
            ]
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.caption("尚未登记相应对象。")
        extra_rows = [
            row
            for row in result.get("states", [])
            if row["property_id"] not in {"location", "held_by"}
        ]
        property_names = {row["id"]: row["name"] for row in operations.records("property")}
        property_names.update({"location": "所在地", "held_by": "持有者", "outfit": "穿戴"})
        if extra_rows:
            st.write("**穿戴、伤势与其它状态**")
            st.dataframe(
                [
                    {
                        "对象": display_item_label(display_map, row["subject_id"], "未知对象"),
                        "属性": property_names.get(
                            row["property_id"],
                            "伤势 · " + row["property_id"][7:]
                            if row["property_id"].startswith("injury:")
                            else row["property_id"],
                        ),
                        "此刻状态": display_item_label(
                            display_map, row.get("value_entity_id"), value_text(row.get("value"))
                        )
                        if row["status"] == "known"
                        else "未知",
                        "判定": STATE_LABELS.get(row["status"], row["status"]),
                        "依据与未知原因": row["reason"],
                    }
                    for row in extra_rows
                ],
                hide_index=True,
                width="stretch",
            )
        if result.get("knowledge"):
            propositions = {
                a.id: f"{a.predicate}：{value_text(a.value)}" for a in operations.assertions()
            }
            st.write("**人物认知（与世界事实分开）**")
            st.dataframe(
                [
                    {
                        "人物": display_item_label(display_map, row.get("holder_id"), "未知人物"),
                        "命题": propositions.get(row.get("assertion_id"), "未找到的命题"),
                        "认知态度": ATTITUDES.get(row.get("attitude"), row.get("attitude")),
                        "判定": STATE_LABELS.get(row.get("status"), row.get("status")),
                        "依据与未知原因": row.get("reason"),
                    }
                    for row in result["knowledge"]
                ],
                hide_index=True,
                width="stretch",
            )
        if result.get("relations"):
            st.write("**关系及有效期**")
            st.dataframe(
                [
                    {
                        "主体": display_item_label(display_map, row.get("subject_id"), "未知对象"),
                        "关系": row.get("predicate"),
                        "客体": display_item_label(display_map, row.get("object_id"), "未知对象"),
                        "判定": STATE_LABELS.get(row.get("status"), row.get("status")),
                        "说明": row.get("reason"),
                    }
                    for row in result["relations"]
                ],
                hide_index=True,
                width="stretch",
            )
        evidence = [
            entry
            for field in ("holders", "locations")
            for entries in result["evidence"][field].values()
            for entry in entries
        ]
        evidence += [
            ev
            for field in ("states", "knowledge", "relations")
            for row in result.get(field, [])
            for ev in row.get("evidence", [])
        ]
        with st.expander("时刻查询证据", expanded=bool(evidence)):
            seen = set()
            for entry in evidence:
                span = (entry["source_id"], entry["start"], entry["end"], entry["source_hash"])
                if span not in seen:
                    seen.add(span)
                    evidence_view(operations, entry)
            if not seen:
                st.caption("当前没有可定位的支持事件；未知不会被当成肯定状态。")
        if result["diagnostics"]:
            with st.expander("时刻查询诊断"):
                st.dataframe(
                    [
                        {"规则": d["rule_id"], "级别": d["severity"], "说明": d["message"]}
                        for d in result["diagnostics"]
                    ],
                    hide_index=True,
                    width="stretch",
                )
        return result


def timeline_tab(operations: Operations):
    st.caption("已采用的事件组成时间轴。相对分钟由人工指定；未定时间单列，插叙保留原文披露顺序。")
    if notice := st.session_state.pop("event-notice", None):
        st.success(notice)
    if st.button("建立时间轴演示候选"):
        operations.propose_timeline_demo()
        st.session_state["event-notice"] = "已建立插叙与钥匙交接演示候选，请到候选审核采用。"
        st.rerun()
    entities = operations.entities()
    names = {entity["id"]: entity["name"] for entity in entities}
    display_map = operations.analysis_display_map()
    order_label = st.radio("排列方式", ["故事发生顺序", "原文披露顺序"], horizontal=True)
    order = "story" if order_label == "故事发生顺序" else "disclosure"
    view = operations.timeline(order)
    filters = st.multiselect(
        "筛选人物、地点或物品", list(names), format_func=lambda ref: display_item_label(display_map, ref, names[ref])
    )
    query_result = time_query_panel(operations, names)
    rows = [
        row
        for row in view["rows"]
        if not filters
        or set(filters)
        & {row.get("actor_id"), row.get("target_id"), row.get("item_id"), row.get("location_id")}
    ]
    if not rows:
        st.info("当前没有符合条件的正式事件。可登记事件，或建立演示候选后审核采用。")
    chart_rows = []
    for index, row in enumerate(rows):
        if order == "story" and (
            row["time_kind"] not in {"point", "interval"} or row["time_start"] is None
        ):
            continue
        start = row["time_start"] if order == "story" else index + 1
        end = (
            (row["time_end"] if row["time_end"] is not None else start)
            if order == "story"
            else start
        )
        chart_rows.append(
            {
                "id": row["id"],
                "title": row["title"],
                "label": f"{index + 1}. {row['title']}",
                "start": start,
                "end": end,
                "status": "待核验" if row.get("status") == "disputed" else "已采用",
            }
        )
    picked_id = None
    if chart_rows:
        encoding = {
            "x": {
                "field": "start",
                "type": "quantitative",
                "title": "故事时间（相对分钟）" if order == "story" else "原文披露次序",
                "scale": {"zero": False},
            },
            "y": {
                "field": "label",
                "type": "nominal",
                "sort": None,
                "title": None,
                "axis": {"labelLimit": 200},
            },
            "color": {"field": "status", "type": "nominal", "title": "状态"},
            "tooltip": [
                {"field": "title", "title": "事件"},
                {"field": "start", "title": "起点"},
                {"field": "end", "title": "终点"},
            ],
        }
        layers = [
            {
                "mark": {"type": "rule", "strokeWidth": 7},
                "encoding": {**encoding, "x2": {"field": "end"}},
            },
            {
                "name": "event_points",
                "mark": {"type": "point", "filled": True, "size": 110},
                "encoding": encoding,
            },
        ]
        if query_result is not None and order == "story":
            layers.append(
                {
                    "data": {"values": [{"minute": query_result["minute"]}]},
                    "mark": {
                        "type": "rule",
                        "color": "#b26d36",
                        "strokeDash": [5, 4],
                        "strokeWidth": 2,
                    },
                    "encoding": {
                        "x": {"field": "minute", "type": "quantitative"},
                        "tooltip": [{"field": "minute", "title": "查询时刻"}],
                    },
                }
            )
        chart = st.vega_lite_chart(
            chart_rows,
            {
                "params": [
                    {
                        "name": "pick_event",
                        "select": {"type": "point", "fields": ["id"], "toggle": False},
                        "views": ["event_points"],
                    }
                ],
                "layer": layers,
            },
            width="stretch",
            height=min(600, max(180, len(chart_rows) * 38)),
            key=f"event-chart-{operations.project_info().revision}-{order}",
            on_select="rerun",
            selection_mode="pick_event",
        )
        if isinstance(chart, dict):
            picked = chart.get("selection", {}).get("pick_event", {})
            if isinstance(picked, list) and picked:
                picked_id = picked[0].get("id")
            elif isinstance(picked, dict):
                ids = picked.get("id", [])
                picked_id = ids[0] if isinstance(ids, list) and ids else None
    if rows:
        if order == "story" and any(row["time_kind"] in {"relative", "unknown"} for row in rows):
            with st.expander("相对先后与未定事件示意", expanded=not chart_rows):
                st.caption("此图没有时间刻度。连线表示已记录的先后关系；无连线不表示同时或先后。")
                displayed = rows[:100]
                shown_ids = {row["id"] for row in displayed}
                dot = [
                    'digraph { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3ee", fontname="sans-serif"];'
                ]
                for row in displayed:
                    node_label = f"{row['title']}\n{row.get('time_label', '时间未定')}"
                    dot.append(
                        f"{json.dumps(row['id'])} [label={json.dumps(node_label, ensure_ascii=False)}];"
                    )
                    for predecessor in row.get("predecessors", []):
                        if predecessor in shown_ids:
                            dot.append(f"{json.dumps(predecessor)} -> {json.dumps(row['id'])};")
                dot.append("}")
                st.graphviz_chart("\n".join(dot), width="content")
                if len(rows) > len(displayed):
                    st.caption(
                        "示意图显示前 100 个事件，可用上方筛选缩小范围。事件表保留全部结果。"
                    )
        st.dataframe(
            [
                {
                    "事件": row["title"],
                    "故事时间": row.get("time_label", "未定"),
                    "披露位置": row.get("disclosure_position"),
                    "状态": {"accepted": "已采用", "disputed": "有争议", "superseded": "已被更新"}.get(row.get("status"), row.get("status")),
                }
                for row in rows
            ],
            hide_index=True,
            width="stretch",
        )
        with st.expander("技术详情 · 事件 ID"):
            st.dataframe([{"事件": row["title"], "ID": row["id"]} for row in rows], hide_index=True, width="stretch")
        if order == "story" and any(row["time_kind"] in {"relative", "unknown"} for row in rows):
            st.caption("相对先后和未定事件保留在事件表中，不为它们虚构分钟坐标。")
        event_ids = [row["id"] for row in rows]
        key = f"event-detail-{operations.project_info().path}"
        selection_key = f"event-chart-last-{operations.project_info().path}-{order}"
        if picked_id in event_ids and picked_id != st.session_state.get(selection_key):
            st.session_state[key] = picked_id
        st.session_state[selection_key] = picked_id
        if st.session_state.get(key) not in event_ids:
            st.session_state[key] = event_ids[0]
        event_id = st.selectbox(
            "查看事件与前后状态",
            event_ids,
            format_func=lambda ref: display_item_label(display_map, ref, "未命名事件"),
            key=key,
        )
        assert event_id is not None
        for evidence in operations.event_evidence(event_id):
            evidence_view(operations, evidence)
        state = operations.event_state(event_id)
        for field, title in (("holders", "物品持有者"), ("locations", "人物所在地")):
            before, after = state["before"][field], state["after"][field]
            refs = sorted(set(before) | set(after))
            if refs:
                st.write(f"**{title}**")
                st.dataframe(
                    [
                        {
                            "对象": names.get(ref, ref),
                            "事件前": names.get(before.get(ref), "未知"),
                            "事件后": names.get(after.get(ref), "未知"),
                        }
                        for ref in refs
                    ],
                    hide_index=True,
                    width="stretch",
                )
        diagnostics = state["diagnostics"]
        with st.expander("完整状态与认知的事件前后"):
            full_state = state
            for phase, phase_title in (("before", "事件前"), ("after", "事件后")):
                st.write(f"**{phase_title}**")
                projection = full_state[phase]
                extra = [
                    row
                    for row in projection["states"]
                    if row["property_id"] not in {"location", "held_by"}
                ]
                for field, data in (
                    ("状态", extra),
                    ("认知", projection["knowledge"]),
                    ("关系", projection["relations"]),
                ):
                    if data:
                        st.caption(field)
                        st.dataframe(data, hide_index=True, width="stretch")
            diagnostics += [item for item in full_state["diagnostics"] if item not in diagnostics]
        if diagnostics:
            st.dataframe(
                [
                    {"规则": d["rule_id"], "级别": d["severity"], "说明": d["message"]}
                    for d in diagnostics
                ],
                hide_index=True,
                width="stretch",
            )
        if st.button("创建事件修正候选"):
            operations.propose_event_correction(event_id)
            st.session_state["event-notice"] = "已建立修正候选，可在候选审核中修改事件时间或动作。"
            st.rerun()
        with st.expander("依据原文修订事件先后"):
            st.caption("只根据明确原文建立相对先后；不按段落位置自动推断。修订作为候选，需在候选审核中采用。")
            prior_events = [
                row
                for row in operations.events()
                if row["id"] != event_id and row.get("status", "accepted") == "accepted"
            ]
            after_event_id = st.selectbox(
                "该事件发生在此事件之后",
                [None, *[row["id"] for row in prior_events]],
                format_func=lambda ref: (
                    "请选择前序事件"
                    if ref is None
                    else next(row["title"] for row in prior_events if row["id"] == ref)
                ),
                key=f"event-order-after-{event_id}",
            )
            order_evidence = evidence_picker(
                operations,
                f"event-order-evidence-{event_id}",
                "支持先后关系的原文片段",
            )
            if st.button(
                "建立先后修订候选",
                disabled=after_event_id is None or order_evidence is None,
                key=f"event-order-submit-{event_id}",
            ):
                assert after_event_id is not None and order_evidence is not None
                operations.propose_event_order(
                    event_id, after_event_id, order_evidence
                )
                st.session_state["event-notice"] = (
                    "先后修订候选已建立；请在候选审核中检查证据并采用。"
                )
                st.rerun()
        with st.expander("依据原文修订状态起止"):
            st.caption("未指定的一端保持原有设置；原本未设置的端点仍是未知，不会自动补值。修订需经候选审核采用。")
            state_rows = operations.records("state")
            if not state_rows:
                st.info("尚无正式状态记录。")
            else:
                state_id = st.selectbox(
                    "状态记录",
                    [row["id"] for row in state_rows],
                    format_func=lambda ref: next(
                        f"{names.get(row['subject_id'], row['subject_id'])} · {row['property_id']}"
                        for row in state_rows
                        if row["id"] == ref
                    ),
                    key=f"state-window-record-{event_id}",
                )
                assert state_id is not None
                selected_state = next(row for row in state_rows if row["id"] == state_id)
                validity = selected_state.get("validity", {})
                current_start = validity.get("start_event_id")
                current_end = validity.get("end_event_id")
                current_event_titles = {row["id"]: row["title"] for row in operations.events()}
                st.caption(
                    "当前起点："
                    + current_event_titles.get(current_start, "未知")
                    + "；当前终点："
                    + current_event_titles.get(current_end, "未知")
                )
                candidate_events = [row for row in operations.events() if row.get("status", "accepted") == "accepted"]
                start_event_id = st.selectbox(
                    "新起点（保持原有设置）",
                    [None, *[row["id"] for row in candidate_events]],
                    format_func=lambda ref: (
                        "保持原有设置"
                        if ref is None
                        else next(row["title"] for row in candidate_events if row["id"] == ref)
                    ),
                    key=f"state-window-start-{event_id}",
                )
                end_event_id = st.selectbox(
                    "新终点（保持原有设置）",
                    [None, *[row["id"] for row in candidate_events]],
                    format_func=lambda ref: (
                        "保持原有设置"
                        if ref is None
                        else next(row["title"] for row in candidate_events if row["id"] == ref)
                    ),
                    key=f"state-window-end-{event_id}",
                )
                window_evidence = evidence_picker(
                    operations,
                    f"state-window-evidence-{event_id}",
                    "支持状态起止的原文片段",
                )
                if st.button(
                    "建立状态起止修订候选",
                    disabled=(
                        start_event_id is None
                        and end_event_id is None
                        or window_evidence is None
                    ),
                    key=f"state-window-submit-{event_id}",
                ):
                    assert window_evidence is not None
                    operations.propose_state_window(
                        state_id,
                        start_event_id=start_event_id,
                        end_event_id=end_event_id,
                        evidence=window_evidence,
                    )
                    st.session_state["event-notice"] = (
                        "状态起止修订候选已建立；请在候选审核中检查证据并采用。"
                    )
                    st.rerun()
    if view["diagnostics"]:
        with st.expander("时间轴检查"):
            st.dataframe(view["diagnostics"], hide_index=True, width="stretch")
    with st.expander("登记事件", expanded=not rows):
        sources = operations.sources()
        if not sources:
            st.info("请先导入原文并登记相关人物、物品或地点。")
            return
        title = st.text_input("事件标题")
        action_label = st.selectbox(
            "动作",
            ["物品交接", "人物移动", "叙事动作", "记录事件", "人物换装", "人物受伤", "处理伤势", "告知信息"],
        )
        actions: dict[
            str, Literal["move", "transfer", "observe", "act", "dress", "injure", "treat", "tell"]
        ] = {
            "物品交接": "transfer",
            "人物移动": "move",
            "叙事动作": "act",
            "记录事件": "observe",
            "人物换装": "dress",
            "人物受伤": "injure",
            "处理伤势": "treat",
            "告知信息": "tell",
        }
        action = actions[action_label]
        people = [entity["id"] for entity in entities if entity["entity_type"] == "person"]

        def label(ref):
            return names[ref] if ref else "请选择"

        actor = st.selectbox(
            "交出者" if action == "transfer" else "行动人物",
            [None, *people],
            format_func=label,
            key="event-actor",
        )
        target = item = location = assertion_id = None
        slot, state_value, persistence, valid_until = "default", None, None, None
        if action == "transfer":
            target = st.selectbox("接收者", [None, *people], format_func=label)
            item = st.selectbox(
                "物品",
                [None, *[e["id"] for e in entities if e["entity_type"] == "item"]],
                format_func=label,
            )
        elif action == "move":
            location = st.selectbox(
                "到达地点",
                [None, *[e["id"] for e in entities if e["entity_type"] == "location"]],
                format_func=label,
            )
        elif action == "act":
            target = st.selectbox(
                "涉及对象（可留空）",
                [None, *[e["id"] for e in entities]],
                format_func=label,
            )
            st.caption("只记录行为，不自动生成状态变化或人物认知。")
        elif action == "dress":
            item = st.selectbox(
                "穿戴物品",
                [None, *[e["id"] for e in entities if e["entity_type"] == "item"]],
                format_func=label,
            )
        elif action in {"injure", "treat"}:
            target = st.selectbox("受伤或接受处理的人物", [None, *people], format_func=label)
            slot = st.text_input("伤势部位或编号", value="default")
            state_value = st.text_input(
                "伤势结果", value="受伤" if action == "injure" else "已处理"
            )
        elif action == "tell":
            target = st.selectbox("获知人物", [None, *people], format_func=label)
            assertions = operations.assertions()
            assertion_id = st.selectbox(
                "告知的命题",
                [None, *[a.id for a in assertions]],
                format_func=lambda ref: next(
                    (
                        f"{names.get(a.subject_id, a.subject_id)} · {a.predicate} · {a.value}"
                        for a in assertions
                        if a.id == ref
                    ),
                    "请选择",
                ),
            )
            st.caption("告知默认记录为已听说，不将消息内容自动确认为世界事实。")
        if action in {"dress", "injure", "treat"}:
            policy = st.selectbox(
                "状态延续方式", ["尚未确定", "延续到下次改变", "仅完成时刻", "到指定时刻"]
            )
            policies: dict[str, Literal["unknown", "until_changed", "instant", "bounded"]] = {
                "尚未确定": "unknown",
                "延续到下次改变": "until_changed",
                "仅完成时刻": "instant",
                "到指定时刻": "bounded",
            }
            persistence = policies[policy]
            if persistence == "bounded":
                valid_until = st.number_input("状态失效分钟", value=60.0)
        time_label = st.selectbox("时间方式", ["尚未确定", "时间点", "时间区间", "在某事件之后"])
        time_kinds: dict[str, Literal["point", "interval", "relative", "unknown"]] = {
            "尚未确定": "unknown",
            "时间点": "point",
            "时间区间": "interval",
            "在某事件之后": "relative",
        }
        time_kind = time_kinds[time_label]
        start = end = after_event = None
        if time_kind in {"point", "interval"}:
            st.caption("指定本作品统一原点后的相对分钟；此处不自动解释原文时间。")
            start = st.number_input("相对分钟", value=0.0, step=1.0)
            if time_kind == "interval":
                end = st.number_input("结束分钟", value=1.0, step=1.0)
        elif time_kind == "relative":
            all_events = operations.events()
            after_event = st.selectbox(
                "前序事件",
                [None, *[e["id"] for e in all_events]],
                format_func=lambda ref: (
                    next(e["title"] for e in all_events if e["id"] == ref) if ref else "请选择"
                ),
            )
        source_id = st.selectbox(
            "事件证据章节",
            [source["id"] for source in sources],
            format_func=lambda ref: next(s["title"] for s in sources if s["id"] == ref),
        )
        assert source_id is not None
        quote = st.text_area("事件证据原句", height=85)
        matches = operations.locate(source_id, quote or "")
        index = st.selectbox(
            "事件证据位置",
            [None, *range(len(matches))],
            format_func=lambda i: (
                "请选择位置" if i is None else f"字符 {matches[i].start}–{matches[i].end}"
            ),
        )
        if index is not None:
            evidence_view(operations, matches[index])
        if st.button("提交事件候选", disabled=index is None or not title.strip()):
            assert index is not None
            operations.propose_event(
                title=title,
                action=action,
                actor_id=actor,
                target_id=target,
                item_id=item,
                location_id=location,
                time_kind=time_kind,
                time_start=start,
                time_end=end,
                after_event_id=after_event,
                evidence=matches[index],
                assertion_id=assertion_id,
                slot=slot,
                state_value=state_value,
                persistence=persistence,
                valid_until=valid_until,
            )
            st.session_state["event-notice"] = "事件已进入候选审核，采用后才显示在正式时间轴。"
            st.rerun()


def analysis_tab(operations: Operations):
    """Browse people, events, and source-backed analysis through Operations."""
    st.caption("从人物、事件和原文证据进入故事资料；内容来自当前正式修订。")
    agent_tab, people_tab, events_tab, classification_tab, search_tab = st.tabs(
        ["叙事 agent 工作区", "人物档案", "事件地图", "分类与故事线", "组合检索"]
    )
    entities = operations.entities()
    display_map = operations.analysis_display_map()
    names = {entity["id"]: entity["name"] for entity in entities}
    people = [entity for entity in entities if entity.get("entity_type") == "person"]
    events = operations.events()

    with people_tab:
        if not people:
            st.info("当前项目还没有人物。导入原文后可在提取交接或实体页登记人物。")
        else:
            person_id = st.selectbox(
                "选择人物", [entity["id"] for entity in people],
                format_func=lambda ref: display_item_label(display_map, ref, names.get(ref, "未知人物")),
                key=f"analysis-person-{operations.project_info().path}",
            )
            assert person_id is not None
            profile = operations.character_profile(person_id)
            entity = profile["entity"]
            st.subheader(entity["name"])
            if entity.get("description"):
                st.write(entity["description"])
            st.caption(f"修订 {profile['revision']} · 别名：{'、'.join(entity.get('aliases', [])) or '无'}")
            if profile.get("coverage"):
                with st.expander("资料覆盖范围"):
                    st.json(profile["coverage"])
            for key, title in (("traits", "人物特点"), ("assertions", "相关事实与主张"),
                               ("relations", "人物关系"), ("states", "状态")):
                rows = profile.get(key, [])
                with st.expander(f"{title} · {len(rows)}", expanded=key == "relations"):
                    if not rows:
                        st.caption("当前修订中没有对应记录。")
                    elif key == "relations":
                        st.dataframe(
                            [{"关系": row["record"].get("predicate", "关系"),
                              "另一方": display_item_label(display_map, row.get("counterpart_id")),
                              "方向": {"incoming": "指向此人物", "outgoing": "从此人物出发"}.get(row.get("direction"), "方向未记录"),
                              "确定性": row["record"].get("certainty", "unknown"),
                              "审核状态": row["record"].get("status", "accepted")}
                             for row in rows], hide_index=True, width="stretch",
                        )
                        for row in rows:
                            for evidence in row["record"].get("evidence", []):
                                evidence_view(operations, evidence)
                    else:
                        st.dataframe(
                            [{"内容": row.get("title") or row.get("predicate")
                              or row.get("property_id") or row.get("name", ""),
                              "类型": {"world": "世界事实", "claim": "人物主张", "knowledge": "人物认知"}.get(row.get("scope"), row.get("kind_label", row.get("kind", ""))),
                              "状态": {"accepted": "已采用", "disputed": "有争议", "superseded": "已被更新"}.get(row.get("status", "accepted"), "待核对")}
                             for row in rows], hide_index=True, width="stretch",
                        )
                        for row in rows:
                            for evidence in row.get("evidence", []):
                                evidence_view(operations, evidence)
            classifications = profile.get("classifications", [])
            with st.expander(f"审核分类 · {len(classifications)}"):
                if classifications:
                    st.dataframe(
                        [{"分类": row.get("category", ""), "说明": row.get("note", ""),
                          "对象类型": row.get("target_kind", "")}
                         for row in classifications], hide_index=True, width="stretch",
                    )
                else:
                    st.caption("当前人物没有已采用的附加分类。")
            person_events = profile.get("events", [])
            st.markdown(f"**相关事件 · {len(person_events)}**")
            if person_events:
                event_choices = [row["event"]["id"] for row in person_events]
                picked_event = st.selectbox(
                    "打开相关事件", event_choices,
                    format_func=lambda ref: display_item_label(display_map, ref, "未命名事件"),
                    key=f"person-event-{person_id}",
                )
                if st.button("在事件地图查看", key=f"open-event-{person_id}"):
                    st.session_state[f"analysis-event-{operations.project_info().path}"] = picked_event
                    st.info("已选择事件；请打开「事件地图」页签查看完整参与者和前后状态。")
            else:
                st.caption("当前修订中没有关联事件。")

    with events_tab:
        if not events:
            st.info("当前项目还没有正式事件。待审核候选不会自动加入地图。")
        else:
            event_key = f"analysis-event-{operations.project_info().path}"
            event_ids = [event["id"] for event in events]
            if st.session_state.get(event_key) not in event_ids:
                st.session_state[event_key] = event_ids[0]
            event_id = st.selectbox(
                "选择事件", event_ids,
                format_func=lambda ref: display_item_label(display_map, ref, "未命名事件"),
                key=event_key,
            )
            assert event_id is not None
            profile = operations.event_profile(event_id)
            event = profile["event"]
            st.subheader(event["title"])
            st.caption(
                f"{event.get('action', '事件')} · {event.get('narrative_type') or '未分类'} · "
                f"时间：{ {'unknown': '未记录', 'relative': '相对时间', 'absolute': '明确时间'}.get(event.get('time_kind'), event.get('time_kind')) } · "
                f"审核：{ {'accepted': '已采用', 'disputed': '有争议', 'superseded': '已被更新'}.get(event.get('status', 'accepted'), '待核对') } · "
                f"修订 {profile['revision']}"
            )
            participants = profile.get("participants", [])
            st.markdown("**参与人物与角色**")
            if participants:
                st.dataframe([{"人物": row["entity"]["name"], "角色": row.get("role", "参与者")}
                              for row in participants], hide_index=True, width="stretch")
                participant_people = [
                    row["entity"]["id"] for row in participants
                    if row.get("entity") and row["entity"].get("entity_type") == "person"
                ]
                if participant_people:
                    participant_id = st.selectbox(
                        "打开参与人物档案", participant_people,
                        format_func=lambda ref: display_item_label(display_map, ref, "未知人物"),
                    )
                    if st.button("选择此人物", key=f"event-open-person-{event_id}"):
                        st.session_state[f"analysis-person-{operations.project_info().path}"] = participant_id
                        st.info("人物已选中；请打开「人物档案」页签查看详情。")
            else:
                st.caption("尚无明确关联人物。")
            with st.expander("事件前后状态", expanded=True):
                state = operations.event_state(event_id)
                for field, title in (("holders", "物品持有者"), ("locations", "人物所在地")):
                    before, after = state["before"][field], state["after"][field]
                    refs = sorted(set(before) | set(after))
                    if refs:
                        st.write(f"**{title}**")
                        st.dataframe([{"对象": display_item_label(display_map, ref),
                                       "事件前": display_item_label(display_map, before.get(ref), "未知"),
                                       "事件后": display_item_label(display_map, after.get(ref), "未知")}
                                      for ref in refs], hide_index=True, width="stretch")
                if state.get("diagnostics"):
                    st.dataframe([{"规则": row["rule_id"], "说明": row["message"]}
                                  for row in state["diagnostics"]], hide_index=True, width="stretch")
            links = profile.get("links", [])
            if links:
                st.markdown("**关联事件**")
                st.dataframe([{"另一事件": display_item_label(display_map, row.get("other_event_id"), "未找到的事件"),
                               "关系": {"precedes": "先于", "follows": "晚于", "echoes": "呼应", "causes": "导致", "contrasts": "对照"}.get(row["record"].get("link_type"), row["record"].get("link_type", "未说明")),
                               "方向": {"incoming": "指向此事件", "outgoing": "从此事件出发"}.get(row.get("direction"), "方向未记录")}
                              for row in links], hide_index=True, width="stretch")
                linked_ids = [row["other_event_id"] for row in links]
                linked_id = st.selectbox(
                    "打开关联事件", linked_ids,
                    format_func=lambda ref: display_item_label(display_map, ref, "未找到的事件"),
                )
                if st.button("选择关联事件", key=f"event-open-linked-{event_id}"):
                    st.session_state[event_key] = linked_id
                    st.info("关联事件已选中；可在上方事件选择器切换查看。")
            storylines = profile.get("storylines", [])
            if storylines:
                st.markdown("**所属故事线**")
                st.dataframe([{"故事线": row.get("title", ""), "类型": row.get("line_type", "")}
                              for row in storylines], hide_index=True, width="stretch")
            classifications = profile.get("classifications", [])
            if classifications:
                st.markdown("**审核分类**")
                st.dataframe([{"分类": row.get("category", ""), "说明": row.get("note", "")}
                              for row in classifications], hide_index=True, width="stretch")
            for evidence in profile.get("evidence", []):
                evidence_view(operations, evidence)

    with classification_tab:
        sources = operations.sources()
        source_filter = st.multiselect(
            "分类统计原文范围", [row["id"] for row in sources],
            format_func=lambda ref: next(row["title"] for row in sources if row["id"] == ref),
            key=f"classification-sources-{operations.project_info().path}",
        )
        table = operations.classification_table(source_filter or None)
        st.caption(f"修订 {table['revision']} · 按原文和分类汇总")
        if table.get("coverage"):
            with st.expander("覆盖范围说明"):
                st.json(table["coverage"])
        rows = table.get("rows", [])
        if rows:
            st.dataframe(
                [{
                    "原文": row["source_title"],
                    "分类维度": {"entity": "人物/实体", "trait": "人物特点", "assertion": "人物主张", "state": "状态", "relation": "人物关系", "event": "叙事事件", "storyline": "故事线", "classification": "审核分类"}.get(row["facet"], row["facet"]),
                    "分类": row["category"],
                    "数量": row["count"],
                } for row in rows],
                hide_index=True, width="stretch",
            )
            object_ids = list(dict.fromkeys(
                object_id for row in rows for object_id in row.get("object_ids", [])
            ))
            if object_ids:
                selected_object = st.selectbox(
                    "打开分类对象", object_ids,
                    format_func=lambda ref: display_item_label(display_map, ref),
                    key=f"classification-object-{operations.project_info().path}",
                )
                if selected_object in {row["id"] for row in events}:
                    if st.button("在事件地图中打开", key="classification-open-event"):
                        st.session_state[f"analysis-event-{operations.project_info().path}"] = selected_object
                        st.info("事件已选中；请打开「事件地图」页签查看详情。")
                elif selected_object in {row["id"] for row in people}:
                    if st.button("查看人物档案", key="classification-open-person"):
                        st.session_state[f"analysis-person-{operations.project_info().path}"] = selected_object
                        st.info("人物已选中；请打开「人物档案」页签查看详情。")
        else:
            st.info("当前选择范围没有可归类的正式对象。事件分类依据叙事类型，推断分类须经候选审核。")
        storylines = [row for row in operations.records() if row["kind"] == "storyline"]
        with st.expander(f"故事线浏览 · {len(storylines)}"):
            if storylines:
                storyline_id = st.selectbox(
                    "选择故事线", [row["id"] for row in storylines],
                    format_func=lambda ref: display_item_label(display_map, ref, "未命名故事线"),
                )
                storyline = next(row for row in storylines if row["id"] == storyline_id)
                line_type_label = {"main": "主线", "branch": "支线", "unknown": "未分类"}.get(str(storyline.get("line_type") or "unknown"), "未分类")
                st.caption(f"{line_type_label} · {storyline.get('description', '')}")
                storyline_events = [event for event in events if event["id"] in storyline.get("event_ids", [])]
                if storyline_events:
                    selected_story_event = st.selectbox(
                        "故事线事件", [row["id"] for row in storyline_events],
                        format_func=lambda ref: display_item_label(display_map, ref, "未命名事件"),
                    )
                    if st.button("定位此故事线事件"):
                        st.session_state[f"analysis-event-{operations.project_info().path}"] = selected_story_event
                        st.info("已选中事件；请打开「事件地图」页签查看参与者和前后状态。")
                else:
                    st.caption("故事线尚未包含事件。")
            else:
                st.caption("当前项目还没有正式故事线。故事线候选采用后才会显示。")
        with st.expander("登记审核分类"):
            record_rows = operations.records()
            target_options = {
                "entity": entities,
                "assertion": [row.model_dump(mode="json") for row in operations.assertions()],
                "relation": [row for row in record_rows if row["kind"] == "relation"],
                "event": events,
                "storyline": [row for row in record_rows if row["kind"] == "storyline"],
            }
            target_kind = st.selectbox(
                "分类对象类型", list(target_options),
                format_func=lambda value: {
                    "entity": "人物／地点／物品", "relation": "关系",
                    "assertion": "人物特点／断言", "event": "叙事事件", "storyline": "故事线",
                }[value],
            )
            targets = target_options[target_kind]
            if targets:
                target_id = st.selectbox(
                    "分类对象", [row["id"] for row in targets],
                    format_func=lambda ref: display_item_label(display_map, ref, "未命名对象"),
                )
                category = st.text_input("分类名称")
                note = st.text_input("分类说明（可选）")
                if st.button("提交分类候选", disabled=not category.strip()):
                    operations.propose_objects([{
                        "kind": "classification", "target_id": target_id,
                        "target_kind": target_kind, "category": category.strip(),
                        "note": note.strip(),
                    }])
                    st.success("分类已提交候选，请在「候选审核」复核后采用。")
            else:
                st.caption("所选类型目前没有可分类对象。")

    with search_tab:
        sources = operations.sources()
        storylines = [row for row in operations.records() if row["kind"] == "storyline"]
        classification_rows = operations.classification_table().get("rows", [])
        categories = list(dict.fromkeys(row["category"] for row in classification_rows))
        narrative_types = list(dict.fromkeys(
            event["narrative_type"] for event in events if event.get("narrative_type")
        ))
        with st.form(f"analysis-search-{operations.project_info().path}"):
            query = st.text_input("关键词（可留空以按筛选条件浏览）")
            selected_sources = st.multiselect(
                "原文范围", [row["id"] for row in sources],
                format_func=lambda ref: next(row["title"] for row in sources if row["id"] == ref),
            )
            selected_entities = st.multiselect(
                "人物、地点或物品", [row["id"] for row in entities],
                format_func=lambda ref: display_item_label(display_map, ref, names.get(ref, "未知对象")),
            )
            selected_kinds = st.multiselect(
                "资料类型", ["source", "entity", "assertion", "event", "state", "relation",
                             "knowledge", "storyline", "classification"],
                format_func=lambda kind: {"source": "原文", "entity": "人物/实体", "assertion": "事实/主张",
                    "event": "叙事事件", "state": "状态", "relation": "关系", "knowledge": "人物认知",
                    "storyline": "故事线", "classification": "审核分类"}.get(kind) or str(kind),
            )
            selected_types = st.multiselect("叙事事件类型", narrative_types)
            selected_categories = st.multiselect("审核分类", categories)
            selected_lines = st.multiselect(
                "故事线", [row["id"] for row in storylines],
                format_func=lambda ref: next(row["title"] for row in storylines if row["id"] == ref),
            )
            selected_statuses = st.multiselect("审核状态", ["accepted", "disputed", "superseded"],
                format_func=lambda status: {"accepted": "已采用", "disputed": "有争议", "superseded": "已被更新"}.get(status) or str(status))
            submitted = st.form_submit_button("检索")
        if submitted:
            result = operations.search_analysis(
                query, source_ids=selected_sources or None,
                entity_ids=selected_entities or None, kinds=selected_kinds or None,
                narrative_types=selected_types or None, categories=selected_categories or None,
                statuses=selected_statuses or None, storyline_ids=selected_lines or None,
            )
            st.caption(f"修订 {result['revision']} · 返回 {len(result['rows'])} 条")
            if result.get("coverage"):
                with st.expander("检索覆盖范围"):
                    st.json(result["coverage"])
            if result["rows"]:
                source_titles = {source["id"]: source["title"] for source in sources}
                st.dataframe([{"类型": {"source": "原文", "entity": "人物/实体", "assertion": "事实/主张", "event": "叙事事件", "state": "状态", "relation": "关系", "knowledge": "人物认知", "storyline": "故事线", "classification": "审核分类"}.get(row["kind"], row["kind"]),
                               "内容": row["title"],
                               "审核状态": {"accepted": "已采用", "disputed": "有争议", "superseded": "已被更新"}.get(row["record"].get("status", "accepted"), "待核对"),
                               "来源": "、".join(source_titles.get(ref, "未找到的原文") for ref in row.get("source_ids", []))}
                              for row in result["rows"]], hide_index=True, width="stretch")
                with st.expander("技术详情 · 检索记录 ID"):
                    st.dataframe([{"内容": row["title"], "记录 ID": row["id"],
                                   "来源 ID": "、".join(row.get("source_ids", []))}
                                  for row in result["rows"]], hide_index=True, width="stretch")
                chosen = st.selectbox(
                    "查看检索证据", [row["id"] for row in result["rows"]],
                    format_func=lambda ref: display_item_label(display_map, ref, "检索结果"),
                )
                row = next(item for item in result["rows"] if item["id"] == chosen)
                for evidence in row.get("evidence", []):
                    evidence_view(operations, evidence)
            else:
                st.info("没有匹配结果。")

    with agent_tab:
        st.caption("agent 仅在你点击启动后读取所选原文范围并提出候选；不会自动接受。")
        sources = operations.sources()
        source_ids = [row["id"] for row in sources]
        selected_sources = st.multiselect(
            "本次分析原文范围", source_ids,
            format_func=lambda ref: next(row["title"] for row in sources if row["id"] == ref),
            key=f"agent-sources-{operations.project_info().path}",
        )
        analysis_task = st.text_area(
            "分析任务目标（可编辑）",
            value=(
                "分析人物特点、人物关系、事件参与者与角色，并梳理主线/支线和分类。"
                "每项结论都应有原文证据；对传闻、推测和不确定关系保留归属与不确定性。"
            ),
            help="首次启动时作为任务目标；从待审核候选启动补查修订时也会复用此目标。",
            key=f"agent-task-{operations.project_info().path}",
        )
        research_rounds = st.number_input(
            "证据检索轮数", min_value=1, value=3, step=1,
            help="达到此轮数后转入草拟阶段；这是阶段转换参数，不是任务总轮数上限。",
            key="agent-v3-research-rounds",
        )
        st.caption(
            "研究、草拟与预检修正分阶段推进；提交阶段会保留并运行预检通过的候选。"
            "新任务不限制工具调用次数，仍可随时取消。"
        )
        saved_service = operations.harness_settings()
        saved_service_tokens = saved_service.get("max_tokens")
        service_output_label = (
            "服务默认（不发送上限参数）"
            if saved_service_tokens is None
            else f"{saved_service_tokens} token · {saved_service.get('token_parameter', '输出字段未记录')}"
        )
        st.caption(
            f"当前已保存共用服务：{saved_service.get('provider')} · {saved_service.get('model')} · "
            f"输出设置：{service_output_label}。agent 默认继承此设置。"
        )
        with st.expander("高级服务参数与可选任务限制", expanded=False):
            service_col, limits_col = st.columns(2)
            with service_col:
                agent_override_draft = f"agent-output-override-draft-{operations.project_info().path}"
                agent_override_widget = f"agent-output-override-{operations.project_info().path}"
                st.session_state.setdefault(agent_override_draft, saved_service_tokens or 8192)
                output_mode = st.selectbox(
                    "Agent 每轮输出设置",
                    ["inherit", "service-default", "override"],
                    format_func=lambda mode: {
                        "inherit": "继承已保存的服务设置",
                        "service-default": "本任务使用服务默认（不发送上限参数）",
                        "override": "本任务指定单次输出上限",
                    }[mode],
                    key=f"agent-output-mode-{operations.project_info().path}",
                    help="服务默认由接口决定，不代表自动使用最大值；本任务指定值不设本地最大值。",
                )
                if output_mode == "override":
                    if agent_override_widget not in st.session_state:
                        st.session_state[agent_override_widget] = st.session_state[agent_override_draft]
                    tokens_per_round = int(st.number_input(
                        "本任务每轮最大输出 Token", min_value=1, step=1,
                        key=agent_override_widget,
                        on_change=remember_number_draft,
                        args=(agent_override_widget, agent_override_draft),
                        help="所选服务是否支持该数值由接口决定。",
                    ))
                else:
                    tokens_per_round = output_mode
                request_seconds = st.number_input(
                    "单次请求超时（秒）", min_value=5, value=180, step=5,
                    key="agent-v3-request-seconds",
                )
            with limits_col:
                limit_rounds = st.checkbox("设置任务总模型轮数上限", value=False, key="agent-v3-limit-rounds")
                max_rounds = (
                    int(st.number_input("任务模型轮数上限", min_value=1, value=12, step=1,
                                        key="agent-v3-max-rounds"))
                    if limit_rounds else None
                )
                limit_tokens = st.checkbox("设置任务总 token 上限", value=False, key="agent-v3-limit-tokens")
                total_tokens = (
                    int(st.number_input("任务 token 上限", min_value=1000, value=100000, step=1000,
                                        key="agent-v3-total-tokens"))
                    if limit_tokens else None
                )
                limit_wall = st.checkbox("设置任务总时长上限", value=False, key="agent-v3-limit-wall")
                wall_seconds = (
                    int(st.number_input("任务时长上限（秒）", min_value=30, value=600, step=30,
                                        key="agent-v3-wall-seconds"))
                    if limit_wall else None
                )
        total_budget_label = (
            f"模型轮数 {max_rounds}" if max_rounds is not None else "模型轮数不限"
        )
        token_budget_label = (
            f"总 token {total_tokens}" if total_tokens is not None else "总 token 不限"
        )
        time_budget_label = (
            f"总时长 {wall_seconds} 秒" if wall_seconds is not None else "总时长不限"
        )
        if tokens_per_round == "inherit":
            output_budget_label = f"继承服务设置：{service_output_label}"
        elif tokens_per_round == "service-default":
            output_budget_label = "本任务使用服务默认（不发送上限参数）"
        else:
            output_budget_label = f"本任务指定 {tokens_per_round} token"
        st.caption(
            f"本次预算摘要：研究轮数 {int(research_rounds)}（阶段阈值） · {total_budget_label} · "
            f"{token_budget_label} · {time_budget_label} · "
            f"每轮输出 {output_budget_label} · 单次请求 {int(request_seconds)} 秒 · 工具次数不限"
        )
        analysis_budget = {
            "research_rounds": int(research_rounds), "max_rounds": max_rounds,
            "max_tokens_per_round": tokens_per_round,
            "max_total_tokens": total_tokens, "wall_seconds": wall_seconds,
            "request_seconds": int(request_seconds),
        }
        api_key = st.text_input("本次 API 密钥（可选）", type="password", key="agent-api-key")
        run_key = f"narrative-agent-run-{operations.project_info().path}"
        run_input_key = f"agent-run-id-{operations.project_info().path}"
        display_map = operations.analysis_display_map()
        if st.button(
            "启动叙事分析", type="primary",
            disabled=not selected_sources,
        ):
            run_id = operations.start_narrative_analysis(
                selected_sources,
                budget=analysis_budget,
                api_key=api_key or None,
                task=analysis_task.strip() or None,
            )
            st.session_state[run_key] = run_id
            st.session_state[run_input_key] = run_id
            st.success("已启动叙事分析。请在下方刷新状态查看进展。")

        saved_run_id = st.session_state.get(run_key, "")
        if run_input_key not in st.session_state:
            st.session_state[run_input_key] = saved_run_id
        deferred_run_key = f"{run_input_key}-pending"
        if deferred_run_key in st.session_state:
            st.session_state[run_input_key] = st.session_state.pop(deferred_run_key)
        deferred_notice_key = f"{run_key}-notice"
        if deferred_notice_key in st.session_state:
            st.success(st.session_state.pop(deferred_notice_key))
        with st.expander("技术详情 · 查看已有任务"):
            run_id = st.text_input("任务 ID", key=run_input_key)
        if run_id:
            try:
                status = operations.narrative_analysis_status(run_id)
            except OperationError as exc:
                st.error(str(exc))
            else:
                current_candidates: dict[str, dict[str, object]] = {
                    row["id"]: row for row in operations.candidates()
                }
                narrative_trace_panel(operations, run_id)
                state_labels = {
                    "queued": "排队中", "running": "运行中", "complete": "完成",
                    "incomplete": "未完成", "cancelled": "已取消", "interrupted": "已中断",
                }
                st.subheader(f"分析任务 · {state_labels.get(status['status'], status['status'])}")
                lineage = status.get("lineage") or {}
                parent_candidate_id = lineage.get("parent_candidate_id")
                if isinstance(parent_candidate_id, str) and parent_candidate_id:
                    parent_status_value = current_candidates.get(parent_candidate_id, {}).get("status")
                    parent_state_label = (
                        "仍待审核" if parent_status_value == "pending"
                        else "已采用" if parent_status_value == "accepted"
                        else "已拒绝" if parent_status_value == "rejected"
                        else "当前状态不可用"
                    )
                    st.info(
                        f"这是基于「{display_item_label(display_map, parent_candidate_id, '源候选')}」的补查修订任务；"
                        f"该源候选当前{parent_state_label}。本次运行生成独立候选。"
                    )
                task_summary = status.get("task")
                if task_summary:
                    st.markdown(f"**本次任务目标：** {task_summary}")
                if status.get("feedback"):
                    feedback_label = (
                        "独立预审建议"
                        if status.get("feedback_origin") == "review_agent_guided"
                        else "用户提供的补查意见"
                    )
                    with st.expander(feedback_label):
                        st.write(status["feedback"])
                phase_labels = {
                    "research": "证据检索", "draft": "草拟", "precheck": "预检",
                    "correction": "预检修正", "submit": "提交候选", "complete": "完成",
                }
                phase = status.get("phase")
                if phase:
                    phase_used = status.get("phase_rounds_used")
                    if isinstance(phase_used, dict):
                        phase_used = phase_used.get(phase)
                    phase_remaining = status.get("phase_remaining")
                    remaining_summary = (
                        "持续至完成" if phase_remaining is None else str(phase_remaining)
                    )
                    st.caption(
                        f"当前阶段：{phase_labels.get(phase, phase)} · "
                        f"本阶段轮次：{phase_used if phase_used is not None else '—'} · 剩余：{remaining_summary}"
                    )
                st.caption(
                    f"供应商 {status.get('provider') or '未知'} · 模型 {status.get('model') or '未知'} · "
                    f"提示版本 {status.get('prompt_version') or '未知'} · "
                    f"轮次 {status.get('rounds', 0)} · 工具动作 {status.get('tool_count', 0)}"
                )
                source_names = [display_item_label(display_map, ref, "未找到的原文")
                                for ref in status.get("source_ids", [])]
                if source_names:
                    st.caption(f"分析原文：{'、'.join(source_names)}")
                usage = status.get("usage", {})
                st.caption(
                    f"Usage：输入 {usage.get('prompt_tokens', 0)} · 输出 "
                    f"{usage.get('completion_tokens', 0)} · 总计 {usage.get('total_tokens', 0)}"
                    f"{' · 不完整' if usage.get('incomplete') else ''}"
                )
                candidate_ids = status.get("candidate_ids", [])
                st.caption(f"产生候选：{len(candidate_ids)} 个")
                precheck_diagnostics = status.get("precheck_diagnostics")
                if precheck_diagnostics:
                    st.markdown("**预检诊断**")
                    st.dataframe(precheck_diagnostics, hide_index=True, width="stretch")
                if status["status"] == "incomplete":
                    st.warning("本次分析未完成；请查看各轮终止原因。未生成的内容不会补写为候选。")
                if status.get("budget", {}).get("max_tools") is not None:
                    st.caption("此历史任务使用当时记录的工具次数上限；新任务不设工具调用次数上限。")
                st.caption("以下为该任务启动时冻结的预算；旧 v1/v2 任务保持其 journal 原值。")
                budget_snapshot = status.get("budget", {})
                historical_output = budget_snapshot.get("max_tokens_per_round")
                historical_config = status.get("config") or {}
                historical_parameter = historical_config.get("token_parameter")
                if isinstance(historical_output, int):
                    st.caption(
                        f"历史每轮输出上限：{historical_output} token · "
                        f"{historical_parameter or '输出字段未记录'}（按该任务冻结配置显示）"
                    )
                elif historical_output == "service-default" or (
                    "max_tokens" in historical_config and historical_config.get("max_tokens") is None
                ):
                    st.caption("历史每轮输出设置：未发送上限参数，由服务决定。")
                elif historical_output == "inherit":
                    inherited = historical_config.get("max_tokens")
                    st.caption(
                        "历史每轮输出设置：继承服务配置 · "
                        + (f"{inherited} token · {historical_parameter or '输出字段未记录'}" if inherited else "服务默认")
                    )
                elif isinstance(historical_output, str) and historical_output.isdigit():
                    st.caption(
                        f"历史每轮输出上限：{historical_output} token · "
                        f"{historical_parameter or '输出字段未记录'}（按该任务冻结配置显示）"
                    )
                if "research_rounds" in budget_snapshot:
                    budget_snapshot = {
                        **budget_snapshot,
                        **{
                            key: "不设上限"
                            for key in ("max_rounds", "max_total_tokens", "wall_seconds")
                            if budget_snapshot.get(key) is None
                        },
                    }
                st.json(budget_snapshot, expanded=False)
                if status.get("config"):
                    with st.expander("技术详情 · 本次冻结服务配置"):
                        st.json({
                            "provider": status.get("provider"), "model": status.get("model"),
                            "prompt_version": status.get("prompt_version"), "config": status["config"],
                        }, expanded=False)
                workspace = status.get("workspace")
                evidence_handles = status.get("evidence_handles")
                if workspace is not None or evidence_handles is not None:
                    st.caption(
                        f"暂存对象：{len(workspace or {})} · 精确证据引用：{len(evidence_handles or {})}"
                    )
                if status.get("error"):
                    st.warning(status["error"])
                for index, step in enumerate(status.get("steps", []), start=1):
                    step_phase = step.get("phase")
                    step_phase_text = f" · {phase_labels.get(step_phase, step_phase)}" if step_phase else ""
                    with st.expander(f"第 {index} 轮{step_phase_text} · {step.get('finish_reason') or '无终止原因'}"):
                        if step.get("phase_round") is not None:
                            st.caption(f"阶段内轮次：{step['phase_round']}")
                        if step.get("diagnostics"):
                            st.dataframe(step["diagnostics"], hide_index=True, width="stretch")
                        if step.get("review_diagnostics"):
                            st.dataframe(step["review_diagnostics"], hide_index=True, width="stretch")
                        if step.get("stage_ids"):
                            st.caption(f"本轮暂存对象：{len(step['stage_ids'])}")
                        if step.get("stage_diagnostics"):
                            st.dataframe(step["stage_diagnostics"], hide_index=True, width="stretch")
                        if step.get("tools"):
                            with st.expander("本轮工具动作"):
                                st.json(step["tools"], expanded=False)
                        if step.get("raw"):
                            with st.expander("技术详情 · 原始模型输出"):
                                st.code(step["raw"], language="json")
                        with st.expander("技术详情 · 本轮指纹"):
                            st.caption(f"输入哈希 {step.get('input_hash', '')}")
                            st.caption(f"配置哈希 {step.get('config_hash', '')}")
                if candidate_ids:
                    st.markdown("**本次候选预览**")
                    selected_candidate = st.selectbox(
                        "选择候选", candidate_ids,
                        format_func=lambda ref: candidate_label(operations, ref, display_map),
                        key=f"agent-candidate-{run_id}",
                    )
                    current_candidate = current_candidates.get(selected_candidate, {})
                    candidate_status = current_candidate.get("status")
                    if candidate_status == "accepted":
                        result_revision = current_candidate.get("result_revision")
                        st.success(
                            "此批已采用到正式资料"
                            + (f"（修订 {result_revision}）" if result_revision else "")
                            + "；下方仍是提交时的模型提案快照。"
                        )
                    elif candidate_status == "rejected":
                        st.info("此批已拒绝；下方保留提交时的模型提案快照，不会写入正式资料。")
                    elif candidate_status == "pending":
                        st.warning("此批仍待人工审核；下方是模型提案，不会自动写入正式资料。")
                    else:
                        st.info("当前审核状态不可用；下方仅显示提交时的模型提案快照。")
                    review = operations.review_candidate(selected_candidate)
                    preview = operations.candidate_preview(selected_candidate)
                    st.caption(
                        f"候选依据修订 {review.change.expected_revision} · 当前修订 {review.current_revision} · "
                        f"候选版本 {review.version}"
                    )
                    with st.expander("技术详情 · 候选 ID"):
                        st.code(selected_candidate)
                    for card in preview.get("cards", []):
                        with st.expander(f"{card['kind_label']} · {card['title']}"):
                            for summary in card.get("summary", []):
                                st.write(summary)
                            for warning in card.get("warnings", []):
                                st.warning(warning["message"])
                            operation = next(
                                op for op in review.change.operations if op.id == card["operation_id"]
                            )
                            for evidence in operation.evidence:
                                evidence_view(operations, evidence)
                    if review.diagnostics:
                        st.dataframe(
                            [{"级别": row.severity, "说明": row.message} for row in review.diagnostics],
                            hide_index=True, width="stretch",
                        )
                    st.caption("审核动作只能在上方「候选审核」页签完成。")
                    pending_ids = {row["id"] for row in operations.candidates(status="pending")}
                    if selected_candidate in pending_ids:
                        feedback_default = (
                            "当前候选覆盖了主要人物、事件和两条主张，但人物特点、人物关系及主线/支线分类不足。"
                            "请继续检索原文、补充准确证据；不要把未明确叙述的关系、人物特点或事件顺序写成事实。"
                        )
                        feedback = st.text_area(
                            "独立预审建议（可编辑）", value=feedback_default,
                            key=f"agent-revision-feedback-{selected_candidate}",
                        )
                        feedback_origin = (
                            "review_agent_guided" if feedback == feedback_default else "user_input"
                        )
                        st.caption(
                            "原样使用时标记为独立预审建议；编辑后标记为用户提供的意见。"
                            "这不表示用户已完成人工审核。"
                        )
                        if st.button(
                            "交 agent 补查修订",
                            disabled=not analysis_task.strip() or not feedback.strip(),
                            key=f"agent-revision-submit-{selected_candidate}",
                        ):
                            revision_source_ids = status.get("source_ids") or selected_sources
                            if not revision_source_ids:
                                st.error("原任务未记录来源范围，请先选择本次分析原文范围。")
                            else:
                                child_run_id = operations.start_narrative_analysis(
                                    revision_source_ids,
                                    budget=analysis_budget,
                                    api_key=api_key or None,
                                    task=analysis_task.strip(),
                                    feedback=feedback.strip(),
                                    seed_candidate_id=selected_candidate,
                                    feedback_origin=feedback_origin,
                                )
                                st.session_state[run_key] = child_run_id
                                st.session_state[deferred_run_key] = child_run_id
                                st.session_state[deferred_notice_key] = (
                                    "已启动独立补查修订任务；源候选保持待审核。"
                                )
                                st.rerun()
                    else:
                        st.caption("此候选已不在待审核状态，不能以它启动补查修订。")


def history_tab(operations: Operations):
    st.caption("修订记录保存正式变更前后内容；拒绝和未采用候选不进入正式资料。")
    for row in reversed(operations.revisions()):
        with st.expander(f"技术详情 · 修订 {row['revision']} · {row['created_at']} UTC"):
            st.json(row["data"])
    with st.expander("审核记录"):
        candidates = operations.candidates()
        display_map = operations.analysis_display_map()
        st.dataframe([{
            "候选批次": candidate_label(operations, row["id"], display_map),
            "审核状态": {"accepted": "已采用", "rejected": "已拒绝", "pending": "待审核"}.get(row["status"], row["status"]),
            "版本": str(row["version"]), "正式修订": str(row.get("result_revision") or "—"),
        } for row in candidates], hide_index=True)
        with st.expander("技术详情 · 候选 ID"):
            st.dataframe([{"ID": row["id"], "状态": row["status"]} for row in candidates], hide_index=True)
    if st.button("生成项目备份"):
        st.session_state["backup"] = (operations.project_info().path, operations.backup_download())
    backup = st.session_state.get("backup")
    if backup and backup[0] == operations.project_info().path:
        st.download_button("下载备份", backup[1].content, file_name=backup[1].filename)
    with st.expander("从备份恢复为新项目"):
        with st.form("restore"):
            upload = st.file_uploader("备份数据库", type=["sqlite3", "db"])
            name = st.text_input("新项目文件名", value="restored")
            if st.form_submit_button("恢复为新项目"):
                if not upload:
                    st.error("请选择备份")
                    return
                operations.restore_upload(upload.getvalue(), name)
                st.success(f"已恢复；在左侧切换到 {name}。")


def model_tab(operations: Operations):
    st.caption(
        "属性、关系、人物认知、规则、场景与制作记录统一经过候选审核。产物观察和验收决定各自保存。"
    )
    labels = {
        "property": "属性定义",
        "classification": "审核分类",
        "event_link": "事件关系",
        "state": "状态记录",
        "relation": "关系",
        "knowledge": "人物认知",
        "storyline": "故事线",
        "rule": "世界规则",
        "scene": "场景",
        "asset_binding": "固定资产绑定",
        "intervention": "外部激励",
        "creative_intent": "人物意图",
        "artifact_observation": "产物观察",
        "artifact_acceptance": "产物验收",
    }
    records = operations.records()
    st.dataframe(
        [
            {"资料类型": label, "正式记录数": sum(row["kind"] == kind for row in records)}
            for kind, label in labels.items()
        ],
        hide_index=True,
        width="stretch",
    )
    names = {entity["id"]: entity["name"] for entity in operations.entities()}
    display_map = operations.analysis_display_map()
    with st.expander("查看与修正资料", expanded=bool(records)):
        if records:
            selected = st.selectbox(
                "正式资料",
                [row["id"] for row in records],
                format_func=lambda ref: display_item_label(display_map, ref, "正式资料"),
            )
            record = next(row for row in records if row["id"] == selected)
            with st.expander("技术详情 · 资料字段"):
                st.json(record, expanded=False)
            for evidence in record.get("evidence", []):
                evidence_view(operations, evidence)
            if st.button("建立资料修正候选"):
                operations.propose_objects(
                    [record], source="correction", note="在候选审核中修改并核对差异。"
                )
                st.success("修正候选已建立，请进入候选审核。")
        else:
            st.info("尚无扩展资料，可在下方登记。")
    with st.expander("登记资料"):
        kind = st.selectbox("资料类型", list(labels), format_func=lambda key: labels[key])
        templates = operations.record_templates()
        assert kind is not None
        st.caption(
            "先填写资料，再核对原文依据。属性定义决定值类型和延续策略；未确定的起止时间留空。"
        )
        if kind in {"state", "relation", "knowledge"} and names:
            subject = st.selectbox(
                "所属对象" if kind != "knowledge" else "认知人物",
                list(names),
                format_func=lambda ref: names[ref],
                key="record-subject",
            )
            payload = {**templates[kind]}
            if kind == "knowledge":
                payload["holder_id"] = subject
                assertions = operations.assertions()
                if not assertions:
                    st.info("请先建立作为认知内容的断言。")
                    return
                payload["assertion_id"] = st.selectbox(
                    "认知命题",
                    [a.id for a in assertions],
                    format_func=lambda ref: next(
                        f"{names.get(a.subject_id, a.subject_id)} · {a.predicate} · {a.value}"
                        for a in assertions
                        if a.id == ref
                    ),
                )
                payload["attitude"] = st.selectbox(
                    "认知态度",
                    ["unknown", "informed", "knows", "believes", "suspects", "denies", "forgotten"],
                    format_func=lambda key: {
                        "unknown": "未知",
                        "informed": "听说",
                        "knows": "知晓",
                        "believes": "相信",
                        "suspects": "怀疑",
                        "denies": "否认",
                        "forgotten": "遗忘",
                    }[key],
                )
                payload["acquisition"] = st.selectbox(
                    "获得方式",
                    ["unknown", "observed", "told", "read", "inferred", "initial"],
                    format_func=lambda key: {
                        "unknown": "未记录",
                        "observed": "亲见",
                        "told": "被告知",
                        "read": "阅读",
                        "inferred": "推断",
                        "initial": "初始设定",
                    }[key],
                )
            elif kind == "relation":
                payload["subject_id"] = subject
                payload["object_id"] = st.selectbox(
                    "关系另一方", list(names), format_func=lambda ref: names[ref]
                )
                payload["predicate"] = st.text_input("关系名称")
                payload["certainty"] = "certain" if st.checkbox("原文明确确认此关系") else "unknown"
            else:
                payload["subject_id"] = subject
                properties = [
                    "location",
                    "held_by",
                    "outfit",
                    "injury:default",
                    *[row["id"] for row in records if row["kind"] == "property"],
                ]
                payload["property_id"] = st.selectbox("状态属性", properties)
                mode = st.selectbox("状态值类型", ["未知", "文字", "数字", "是／否", "实体引用"])
                payload["value"] = None
                if mode == "实体引用":
                    payload["value_entity_id"] = st.selectbox(
                        "状态所指对象", list(names), format_func=lambda ref: names[ref]
                    )
                elif mode == "文字":
                    payload["value"] = st.text_input("状态文字")
                elif mode == "数字":
                    payload["value"] = st.number_input("状态数值", value=0.0)
                elif mode == "是／否":
                    payload["value"] = st.radio(
                        "状态真假", [True, False], format_func=lambda value: "是" if value else "否"
                    )
                payload["persistence"] = st.selectbox(
                    "记录延续策略",
                    ["unknown", "until_changed", "instant", "bounded"],
                    format_func=lambda key: {
                        "unknown": "未确定",
                        "until_changed": "到下次改变",
                        "instant": "仅起始时刻",
                        "bounded": "明确有效区间",
                    }[key],
                )
            payload["validity"] = {}
            if st.checkbox("有效起始时刻已确定"):
                payload["validity"]["start"] = st.number_input("资料开始分钟", value=0.0)
            if st.checkbox("有效结束时刻已确定"):
                payload["validity"]["end"] = st.number_input("资料结束分钟", value=60.0)
        else:
            text = st.text_area(
                "资料字段（可编辑）",
                json.dumps(templates[kind], ensure_ascii=False, indent=2),
                height=250,
                key=f"record-json-{kind}",
            )
            payload = json.loads(text)
            st.caption("对象 ID 可在实体、断言和正式资料中查看。完整字段说明可在下方下载。")
        source_rows = operations.sources()
        if source_rows:
            source_id = st.selectbox(
                "资料证据章节",
                [row["id"] for row in source_rows],
                format_func=lambda ref: next(
                    row["title"] for row in source_rows if row["id"] == ref
                ),
            )
            quote = st.text_input("资料证据原句（创作或制作记录可留空）")
            matches = operations.locate(source_id, quote) if source_id else []
            chosen = st.selectbox(
                "资料证据位置",
                [None, *range(len(matches))],
                format_func=lambda index: (
                    "请选择"
                    if index is None
                    else f"字符 {matches[index].start}–{matches[index].end}"
                ),
            )
            if chosen is not None:
                payload["evidence"] = [matches[chosen].model_dump()]
                evidence_view(operations, matches[chosen])
        if st.button("提交资料候选"):
            operations.propose_objects([payload])
            st.success("资料已进入候选审核，采用后才参与查询。")
    with st.expander("场景交付包"):
        scenes = [row for row in records if row["kind"] == "scene"]
        if scenes:
            scene_id = st.selectbox(
                "交付场景",
                [row["id"] for row in scenes],
                format_func=lambda ref: next(row["title"] for row in scenes if row["id"] == ref),
            )
            assert scene_id is not None
            draft = st.checkbox("包含缺口的草稿", value=True)
            if st.button("生成固定修订场景包"):
                package = operations.scene_package(scene_id, draft=draft)
                for gap in package["unresolved"]:
                    st.warning(gap)
                st.download_button(
                    "下载场景包",
                    json.dumps(package, ensure_ascii=False, indent=2),
                    "scene-package.json",
                    "application/json",
                )
        else:
            st.caption("登记并采用场景后可生成入场／出场状态与固定资产引用。")
    st.download_button(
        "下载完整数据契约",
        json.dumps(operations.model_schema(), ensure_ascii=False, indent=2),
        "narrative-model-v3.json",
        "application/json",
    )
    if st.button("检查正式资料"):
        st.dataframe(operations.validate_project(), hide_index=True, width="stretch")


def main():
    from narrative.graph_ui import main as graph_main

    graph_main()


if __name__ == "__main__":
    main()
