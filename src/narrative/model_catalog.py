"""Small, reviewed model picker lists for providers already supported by the app.

Lists checked against official provider documentation on 2026-10-18. These are
convenience suggestions, not live entitlement checks; users may enter any model
or deployment ID supported by their account.
"""

from typing import TypedDict


class ModelOption(TypedDict):
    id: str
    name: str


# Ark Coding Plan accepts model names from its subscription catalog. The
# separate general Ark API uses deployment endpoint IDs and intentionally has
# no generic model-name suggestions here.
MODEL_OPTIONS: dict[str, list[ModelOption]] = {
    "ark": [
        {"id": "Doubao-Seed-2.1-pro", "name": "豆包 Seed 2.1 Pro"},
        {"id": "doubao-seed-2.1-lite", "name": "豆包 Seed 2.1 Lite"},
        {"id": "doubao-seed-2.0-mini", "name": "豆包 Seed 2.0 Mini"},
        {"id": "doubao-seed-evolving", "name": "豆包 Seed Evolving"},
        {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash"},
        {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
        {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
        {"id": "kimi-k3", "name": "Kimi K3"},
        {"id": "kimi-k2.8-preview", "name": "Kimi K2.8 Preview"},
        {"id": "glm-5.3", "name": "GLM 5.3"},
        {"id": "glm-5.3-flash", "name": "GLM 5.3 Flash"},
        {"id": "minimax-m3", "name": "MiniMax M3"},
    ],
    "deepseek": [
        {"id": "deepseek-flash", "name": "DeepSeek Flash"},
        {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
    ],
    # Only OpenAI-compatible Chat Completions models are listed. Models whose
    # Go endpoint requires Responses or Anthropic Messages are excluded.
    "opencode-go": [
        {"id": "glm-5.3-flash", "name": "GLM 5.3 Flash"},
        {"id": "glm-5.3", "name": "GLM 5.3"},
        {"id": "glm-5.2", "name": "GLM 5.2"},
        {"id": "kimi-k3", "name": "Kimi K3"},
        {"id": "kimi-k2.7-code", "name": "Kimi K2.7 Code"},
        {"id": "longcat-2.0", "name": "LongCat 2.0"},
        {"id": "mimo-v2.6-flash", "name": "MiMo V2.6 Flash"},
        {"id": "mimo-v2.6-pro", "name": "MiMo V2.6 Pro"},
        {"id": "mimo-v2.5", "name": "MiMo V2.5"},
        {"id": "mimo-v2.5-pro", "name": "MiMo V2.5 Pro"},
        {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash"},
        {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
        {"id": "hy4-preview", "name": "Hy4 Preview"},
        {"id": "hy3", "name": "Hy3"},
        {"id": "space-bunny", "name": "Space Bunny"},
    ],
    "openai": [
        {"id": "gpt-5.4", "name": "GPT-5.4"},
        {"id": "gpt-5.4-mini", "name": "GPT-5.4 mini"},
        {"id": "gpt-5.4-nano", "name": "GPT-5.4 nano"},
        {"id": "gpt-4.1", "name": "GPT-4.1"},
        {"id": "gpt-4.1-mini", "name": "GPT-4.1 mini"},
    ],
    "qwen": [
        {"id": "qwen3.8-max", "name": "Qwen 3.8 Max"},
        {"id": "qwen3.8-flash", "name": "Qwen 3.8 Flash"},
        {"id": "qwen3.7-max", "name": "Qwen 3.7 Max"},
        {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus"},
        {"id": "qwen3.6-plus", "name": "Qwen 3.6 Plus"},
        {"id": "qwen3.5-plus", "name": "Qwen 3.5 Plus"},
        {"id": "qwen3.5-flash", "name": "Qwen 3.5 Flash"},
    ],
    "siliconflow": [
        {"id": "deepseek-ai/DeepSeek-V4-Flash", "name": "DeepSeek V4 Flash"},
        {"id": "Pro/deepseek-ai/DeepSeek-V4-Pro", "name": "DeepSeek V4 Pro"},
        {"id": "deepseek-ai/DeepSeek-V3.2", "name": "DeepSeek V3.2"},
        {"id": "Qwen/Qwen3.6-27B", "name": "Qwen 3.6 27B"},
        {"id": "Qwen/Qwen3.6-35B-A3B", "name": "Qwen 3.6 35B A3B"},
        {"id": "Pro/moonshotai/Kimi-K2.6", "name": "Kimi K2.6 Pro"},
    ],
    # Ollama model names must already exist in the local Ollama library.
    "ollama": [
        {"id": "qwen3.8:27b", "name": "Qwen 3.8 · 27B（本地）"},
        {"id": "qwen3.5:9b", "name": "Qwen 3.5 · 9B（本地）"},
        {"id": "gpt-oss:20b", "name": "gpt-oss · 20B（本地）"},
        {"id": "deepseek-r1:8b", "name": "DeepSeek R1 · 8B（本地）"},
    ],
}

MODEL_CATALOG_SOURCES = {
    "ark": "https://console.volcengine.com/ark/region:cn-beijing/docs/coding-plan-personal-get-started",
    "ark-standard": "https://www.volcengine.com/docs/82379",
    "deepseek": "https://api-docs.deepseek.com/api/list-models/",
    "opencode-go": "https://docs.opencode.ai/docs/go/",
    "openai": "https://developers.openai.com/api/docs/models/gpt-5.4",
    "qwen": "https://help.aliyun.com/zh/model-studio/model-list-text-generation/",
    "siliconflow": "https://docs.siliconflow.cn/docs/api/chat-completions-post",
    "ollama": "https://ollama.com/library",
}


def model_options(provider: str) -> list[ModelOption]:
    """Return a copy so callers cannot mutate the built-in catalog."""
    return [{"id": item["id"], "name": item["name"]} for item in MODEL_OPTIONS.get(provider, [])]
