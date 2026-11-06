"""One real short scene follows effective agreement, not project defaults alone."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed as scene_seed

TASK = "请读取指定场次上下文，按实际生效的统一创作约定写极短正文：一个action和一个dialogue，总字约80字以内。保留夜晚旧车站的铜钥匙交接，别增加独立剧情。注意工程默认和本剧本覆盖的区别；空白覆盖表示该项不设限制。使用submit_script_unit按当前依据保存，完成即报告，不改任何约定或其他对象。"


def seed(ops):
    f = scene_seed(ops)
    other = ops.create_screenplay("独立对照剧本", reason="创作约定隔离对照")["screenplay"]
    initial = ops.load_creative_agreement(f["screenplay_id"])
    defaults = initial["project_defaults"] | {"tone": "轻快", "audiovisual_style": "用静物和自然声表现，不用解释性旁白", "dialogue_style": "对白不直说意图，简短有潜台词", "production_constraints": "本工程默认只能白天拍摄"}
    agreement = ops.save_creative_agreement(defaults, initial["project_revision"], "隔离创作约定覆盖验收", screenplay_id=f["screenplay_id"], script_overrides={"tone": "克制紧张", "production_constraints": ""}, expected_script_revision=initial["script_revision"])
    f["agreement"] = agreement
    f["other_script_id"] = other["id"]
    f["other_before"] = ops.read_screenplay(other["id"])
    f["other_agreement_before"] = ops.load_creative_agreement(other["id"])
    return f


def validate(ops, f, screenplay, calls, report):
    unit = ops.read_script_segment(f["segment_id"])["units"][0]
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    contexts = [s["result"] for s in audit["steps"] if s["tool"] == "read_script_unit_context" and s.get("ok")]
    from narrative.tool_api import TOOLS
    writes = [s for s in audit["steps"] if s.get("ok") and s["tool"] in TOOLS and TOOLS[s["tool"]].writes]
    agreement = ops.load_creative_agreement(f["screenplay_id"])
    report["agreement_evidence"] = {"contexts": contexts, "unit": unit, "successful_writes": writes, "agreement": agreement, "semantic_review": "Read actual action/dialogue for restrained tension, natural sound and indirect dialogue. Do not use lexical markers as artistic proof."}
    return {"other_script_unchanged": ops.read_screenplay(f["other_script_id"]) == f["other_before"] and ops.load_creative_agreement(f["other_script_id"]) == f["other_agreement_before"],
            "agreement_preserved": agreement == f["agreement"],
            "effective_override_and_clear": agreement["effective"]["tone"] == "克制紧张" and agreement["effective"]["production_constraints"] == "",
            "inheritance_preserved": agreement["effective"]["audiovisual_style"] == agreement["project_defaults"]["audiovisual_style"] and agreement["effective"]["dialogue_style"] == agreement["project_defaults"]["dialogue_style"],
            "context_exact_effective": bool(contexts) and all(c["creative_agreement"] == {"fields": agreement["effective"], "sources": agreement["effective_sources"]} for c in contexts),
            "single_target_body_commit": len(writes) == 1 and writes[0]["tool"] == "submit_script_unit" and writes[0]["args"]["unit_id"] == f["unit_id"],
            "submitted_effective_basis_current": unit["basis_state"] == "current" and bool(contexts) and bool(writes) and writes[-1]["args"].get("context_fingerprint") == contexts[-1]["context_fingerprint"],
            "action_and_dialogue": {"action", "dialogue"}.issubset({b["type"] for b in unit["blocks"]})}


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "effective-creative-agreement/1"
    qa.FIXTURE_ORIGIN = "Controlled consistent night-station key handoff; project defaults and script overrides are host-set, only short body is real output."
    return qa.main(validate=validate, required_tools=["read_script_unit_context", "submit_script_unit"],
                   target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]},
                   prompt_factory=lambda f: TASK + "\nunit_id=" + f["unit_id"])


if __name__ == "__main__":
    raise SystemExit(main())
