"""请求体整形：输出上限、模型名、温度都由网关写进去，运行时那侧不带这些。"""

from __future__ import annotations

from narrative.harness import HarnessSettings
from narrative.model_gateway import adapt_request


def test_temperature_is_written_into_the_upstream_body() -> None:
    settings = HarnessSettings(provider="deepseek", model="deepseek-chat", temperature=0.2)
    body = adapt_request({"model": "whatever", "messages": []}, settings)
    assert body["temperature"] == 0.2


def test_temperature_can_be_left_to_the_provider() -> None:
    """设成 null 就不写：有些供应商不接受这个参数。"""
    settings = HarnessSettings(provider="deepseek", model="deepseek-chat", temperature=None)
    body = adapt_request({"model": "whatever", "messages": []}, settings)
    assert "temperature" not in body


def test_temperature_is_bounded() -> None:
    import pytest

    with pytest.raises(ValueError):
        HarnessSettings(provider="deepseek", model="deepseek-chat", temperature=3)


def test_off_turns_thinking_off_instead_of_naming_a_level() -> None:
    """`off` 不是推理等级：运行时只认 low/medium/high，传 off 会被直接拒。

    供应商自己的开关是 `thinking.type = disabled`，由网关补上——推理 token 归零。
    """
    settings = HarnessSettings(
        provider="ark", model="ark-code-latest", reasoning_effort="off"
    )
    body = adapt_request({"model": "whatever", "messages": [], "reasoning_effort": "low"},
                         settings)
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


def test_a_normal_level_still_goes_through_as_itself() -> None:
    settings = HarnessSettings(
        provider="ark", model="ark-code-latest", reasoning_effort="low"
    )
    body = adapt_request({"model": "whatever", "messages": []}, settings)
    assert body["reasoning_effort"] == "low"
    assert "thinking" not in body
