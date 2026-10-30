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
