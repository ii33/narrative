"""Provider presets and wire parameter policy; no UI or credentials."""

PRESETS = {
    "ark": {
        "name": "火山方舟 Coding Plan",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "model": "Doubao-Seed-2.1-pro",
        "key_env": "ARK_API_KEY",
        "reasoning_effort": "low",
        "token_parameter": "max_tokens",
        "notes": "当前已使用的套餐地址；通用按量 API 请选择火山方舟通用服务。",
        "docs_url": "https://www.volcengine.com/docs/82379",
    },
    "ark-standard": {
        "name": "火山方舟通用服务",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "",
        "key_env": "ARK_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_tokens",
        "notes": "填写账号可用的精确模型或推理接入点 ID；与 Coding Plan 分开保存。",
        "docs_url": "https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk?lang=zh",
    },
    "deepseek": {
        "name": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "model": "",
        "key_env": "DEEPSEEK_API_KEY",
        "reasoning_effort": "low",
        "token_parameter": "max_tokens",
        "notes": "填写控制台可用的模型 ID；推理强度按所用模型支持情况设置。",
        "docs_url": "https://api-docs.deepseek.com/guides/thinking_mode/",
    },
    "opencode-go": {
        "name": "OpenCode Go",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "model": "",
        "key_env": "OPENCODE_GO_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_tokens",
        "notes": "填写 Go 支持 Chat Completions 的精确模型 ID（不加 opencode-go/ 前缀）。会话标识自动发送。Go 主要面向编程代理；小说提取的用途支持需向服务方确认。",
        "docs_url": "https://opencode.ai/docs/go/",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "key_env": "OPENAI_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_completion_tokens",
        "notes": "使用支持 Chat Completions 的模型；推理参数默认不发送。",
        "docs_url": "https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create",
    },
    "qwen": {
        "name": "阿里云百炼 / 通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "",
        "key_env": "DASHSCOPE_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_tokens",
        "notes": "默认北京区域；其他区域请填写对应地址，密钥需与区域一致。",
        "docs_url": "https://help.aliyun.com/en/model-studio/base-url",
    },
    "siliconflow": {
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "",
        "key_env": "SILICONFLOW_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_tokens",
        "notes": "模型名需包含平台要求的完整名称；推理参数默认不发送。",
        "docs_url": "https://docs.siliconflow.cn/docs/api/chat-completions-post",
    },
    "ollama": {
        "name": "Ollama 本地模型",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "",
        "key_env": "OLLAMA_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_tokens",
        "notes": "先启动 Ollama 并下载模型，填写本机模型名称；本机默认可不填密钥。",
        "docs_url": "https://docs.ollama.com/api/openai-compatibility",
    },
    "custom": {
        "name": "自定义 OpenAI 兼容服务",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "",
        "key_env": "NARRATIVE_API_KEY",
        "reasoning_effort": None,
        "token_parameter": "max_tokens",
        "notes": "需支持 Chat Completions 流式输出；可自定义密钥环境变量和输出上限字段。",
        "docs_url": "",
    },
}


def preset(provider: str) -> dict:
    if provider not in PRESETS:
        raise ValueError("未知模型供应商，请从列表中选择")
    return dict(PRESETS[provider])


def catalog() -> list[dict]:
    return [{"id": provider, **values} for provider, values in PRESETS.items()]
