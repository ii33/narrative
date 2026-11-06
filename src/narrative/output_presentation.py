"""Visible process notes are presentation metadata, never private reasoning."""
OUTPUT_CONTRACT_VERSION = "task-output/2"
OUTPUT_CONTRACT = """本轮输出约定：入库数量、编号清单、修订号和变更账目由程序显示，不要再复述，也不另写终场总结。业务执行时要有简短公开过程说明：开始时说明处理重点，有实质进展、关键判断、问题或方向变化时及时简述，避免较长执行只有工具输出；说明写在正常公开text内容中，可与工具调用并列，不仅写在私密reasoning/thinking块里。不必每次工具调用都说明，只保留事项、结论性判断和存疑理由；不要展示私密详细推理或逐步内心推演。完成全部必要步骤后自然结束，不为凑说明再调用工具。普通问答，以及用户明确要求在对话中交付的创作正文、分析结果或解释仍须正常提供，不得把它们替换成提交账目。本约定适用于当前轮，历史回复格式不作为继续复述账目的依据。"""


def present_message(event, output_presentation="answer"):
    """Fold public tool commentary or explicit thought metadata, never prose guesses.

    output_presentation is the captured task preference, not permission to hide
    every final answer in a writing task. Plain final prose remains an answer.
    """
    if output_presentation not in {"answer", "thought"}:
        raise ValueError("output_presentation必须为answer或thought")
    process = any(isinstance(block, dict) and block.get("type") in {"tool_use", "tool_call", "tool-call"}
                  for block in event.get("content", []) or [])
    return event | {"display_kind": "thought" if process or event.get("display_kind") == "thought" else "answer"}
