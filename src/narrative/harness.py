"""DeepSeek Harness SDK adapter. No UI, database, or narrative mutations."""

import json
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, StrictInt, model_validator

from .base_model import Model
from .model_gateway import COMPACTION_MODEL, RUNTIME_EFFORTS, ModelGateway
from .prompt_defaults import DEFAULT_PROMPTS
from .providers import preset

SDK_VERSION = "0.2.1rc2"

CONVERSATION_SYSTEM_PROMPT = DEFAULT_PROMPTS["system"]["text"]


def project_runtime(directory: Path) -> tuple[Path, dict[str, str]]:
    """Select the installed project binary and keep its caches out of user globals."""
    import deepseek_harness
    import deepseek_harness_runtime

    prefix = Path(sys.prefix).resolve()
    modules = (deepseek_harness, deepseek_harness_runtime)
    for module in modules:
        module_file = module.__file__
        if not module_file or not Path(module_file).resolve().is_relative_to(prefix):
            raise ValueError("Harness 必须安装在当前项目环境中，不能使用系统安装")
    binary = deepseek_harness_runtime.bundled_runtime_path().resolve()
    if not binary.is_relative_to(prefix):
        raise ValueError("Harness 运行时必须来自当前项目环境，不能使用外部程序")
    # Native loaders append package/version/ABI paths. Keep this shared cache
    # short even when a source slice's audit directory is deeply nested.
    cache = prefix / ".harness"
    for name in ("native", "resources"):
        (cache / name).mkdir(parents=True, exist_ok=True)
    root = (directory / "runtime").resolve()
    for name in ("cache", "tmp", "config", "data"):
        (root / name).mkdir(parents=True, exist_ok=True)
    # SDK overlays this mapping onto the parent environment. Empty values
    # neutralize inherited runtime switches without mutating os.environ.
    env = {name: "" for name in os.environ if name.upper().startswith(("DSH_", "NODE_", "NARB_"))}
    paths = [str(binary.parent)]
    if os.name == "nt":
        paths.append(str(Path(os.environ["SystemRoot"]) / "System32"))
    env.update({
        "PATH": os.pathsep.join(paths),
        "DSH_RUNTIME_MODE": "exe",
        "DSH_RESOURCE_CACHE": str(cache / "resources"),
        "NARB_NATIVE_CACHE_DIR": str(cache / "native"),
        "NARB_DISABLE_NATIVE_CACHE": "0",
        "NARB_DISABLE_OPTIONAL_PACKAGE": "0",
        "NARB_DISABLE_LOCAL_BUILD": "1",
        "NARB_BACKEND": "auto",
        "TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "APPDATA": str(root / "config"), "LOCALAPPDATA": str(root / "cache"),
    })
    return binary, env


class HarnessSettings(Model):
    provider: str = "ark"
    base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3"
    model: str = "Doubao-Seed-2.1-pro"
    reasoning_effort: str | None = "low"
    max_tokens: StrictInt | None = Field(default=65_536, gt=0)
    context_window: StrictInt = Field(default=1_000_000, gt=0)
    output_reserve: StrictInt = Field(default=65_536, ge=0)
    compaction_reserve: StrictInt = Field(default=131_072, ge=0)
    compaction_max_tokens: StrictInt = Field(default=32_768, gt=0)
    timeout_seconds: int = Field(
        default=120, ge=5, le=600, description="网络连接或连续无响应的空闲超时秒数；不是任务总时限",
    )
    key_env: str = "DEEPSEEK_API_KEY"
    token_parameter: str = "max_tokens"
    # 提取要的是稳定：同一段原文跑两遍不该一个给 43 个方面、一个给 32 个。
    # 温度压在这里，网关在转发时写进请求体（运行时自己不带这个参数）。
    temperature: float | None = Field(default=0.2, ge=0, le=2)

    @model_validator(mode="before")
    @classmethod
    def provider_defaults(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        if "provider" in values:
            defaults = preset(values["provider"])
            for key in ("base_url", "model", "reasoning_effort", "key_env", "token_parameter"):
                values.setdefault(key, defaults[key])
        else:
            # Legacy projects keep their exact endpoint, model and key variable.
            address = values.get("base_url", cls.model_fields["base_url"].default)
            values["provider"] = (
                "ark"
                if isinstance(address, str) and "ark.cn-beijing.volces.com/api/plan/v3" in address
                else "custom"
            )
        return values

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.context_window <= self.effective_output_reserve + self.compaction_reserve:
            raise ValueError("上下文窗口必须大于有效输出预留与压缩预留之和")
        if self.compaction_max_tokens >= self.context_window:
            raise ValueError("压缩最大输出必须小于上下文窗口，为摘要输入保留空间")
        preset(self.provider)
        self.base_url = self.base_url.strip().rstrip("/").removesuffix("/chat/completions")
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("模型地址必须是无凭据、查询参数和片段的 HTTP(S) API 地址")
        if url.path.endswith(("/responses", "/messages")) or ":generateContent" in url.path:
            raise ValueError("当前适配 Chat Completions；请填写兼容 API 的基础地址")
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", self.key_env):
            raise ValueError("请填写密钥环境变量名称（大写字母、数字和下划线），不要填写密钥值")
        if self.token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("输出上限字段仅支持 max_tokens 或 max_completion_tokens")
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("请填写精确模型 ID")
        self.reasoning_effort = (
            self.reasoning_effort.strip() or None if self.reasoning_effort else None
        )
        return self

    @property
    def effective_output_reserve(self) -> int:
        return max(self.output_reserve, self.max_tokens or 0)

    @property
    def compaction_threshold(self) -> int:
        return self.context_window - self.effective_output_reserve - self.compaction_reserve


def runtime_patch(settings: HarnessSettings) -> list[dict]:
    """Supported 0.1.5rc1 plugin fields, composed after the extraction safety patch."""
    threshold_ratio = settings.compaction_threshold / settings.context_window
    # The plugin floors W * ratio; division roundoff must not move the trigger by a token.
    if math.floor(settings.context_window * threshold_ratio) < settings.compaction_threshold:
        threshold_ratio = math.nextafter(threshold_ratio, 1.0)
    return [
        {
            "id": "llm-deepseek",
            "config": {
                "apiKeyEnv": "DEEPSEEK_API_KEY",
                "defaultContextWindow": settings.context_window,
                "streamIdleTimeoutMs": settings.timeout_seconds * 1000,
                # Exact catalog capacities win over defaultContextWindow in this runtime.
                "models": [
                    {"id": settings.model, "contextWindow": settings.context_window},
                    {"id": COMPACTION_MODEL, "contextWindow": settings.context_window},
                ],
            },
        },
        {
            "insert": [
                {"id": "token-meter", "name": "@deepseek-ai/dsh-token-meter"},
                {
                    "id": "compaction-basic",
                    "name": "@deepseek-ai/dsh-compaction-basic",
                    "config": {
                        "thresholdRatio": threshold_ratio,
                        # Keep the shipped 16% tail, bounded below the configured trigger.
                        "retainRatio": min(0.16, threshold_ratio / 2),
                        "maxTokens": settings.compaction_max_tokens,
                        "summarizationProvider": "deepseek-official",
                        "summarizationModel": COMPACTION_MODEL,
                        "compactionRetries": 0,
                        "maxOverflowRetries": 0,
                        "auto": True,
                    },
                },
            ]
        },
    ]


def effective_runtime_config(settings: HarnessSettings) -> dict:
    """Non-sensitive configuration facts suitable for durable run metadata."""
    plugin = runtime_patch(settings)[-1]["insert"][-1]["config"]
    return {
        **{
            key: getattr(settings, key)
            for key in (
                "context_window",
                "output_reserve",
                "compaction_reserve",
                "compaction_max_tokens",
                "max_tokens",
            )
        },
        "effective_output_reserve": settings.effective_output_reserve,
        "compaction_threshold_tokens": settings.compaction_threshold,
        "threshold_ratio": plugin["thresholdRatio"],
        "retain_ratio": plugin["retainRatio"],
        "compaction_plugin": "@deepseek-ai/dsh-compaction-basic",
        "compaction_retries": 0,
        "overflow_retries": 0,
        "llm_retry_enabled": False,
    }


@dataclass(frozen=True)
class HarnessReply:
    raw: str
    metadata: dict

    @property
    def commit_summary(self) -> dict | None:
        return self.metadata.get("commit_summary")

    @property
    def commit_summary_text(self) -> str:
        return self.metadata.get("commit_summary_text", "")


def _sdk_error_diagnostics(events: list[dict], secrets_to_redact: tuple[str, ...]) -> list[dict]:
    """Keep only short error fields from SDK events, never event payloads or prompts."""
    diagnostics = []
    for event in events:
        event_type = event.get("type")
        if event_type not in {"turn/end", "session/error", "agent/error", "error"}:
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        reason = data.get("reason") if event_type == "turn/end" else data
        if not isinstance(reason, dict) or reason.get("kind") not in {None, "error"}:
            continue
        error = reason.get("error")
        detail = error if isinstance(error, dict) else reason
        row = {"event_type": event_type}
        if isinstance(reason.get("kind"), str):
            row["kind"] = reason["kind"]
        for key in ("kind", "code", "message"):
            value = detail.get(key)
            if not isinstance(value, str) and key == "message" and isinstance(error, str):
                value = error
            if not isinstance(value, str) or not value:
                continue
            for secret in secrets_to_redact:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
            row[key] = value[:500]
        if len(row) > 1:
            diagnostics.append(row)
        if len(diagnostics) >= 4:
            break
    return diagnostics


def _windows_key(env_name: str = "DEEPSEEK_API_KEY") -> tuple[str, str]:
    if os.name != "nt":
        return "", "none"
    import winreg

    for root, path, source in (
        (winreg.HKEY_CURRENT_USER, "Environment", "user_environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            "machine_environment",
        ),
    ):
        try:
            with winreg.OpenKey(root, path) as registry:
                value, _ = winreg.QueryValueEx(registry, env_name)
            if isinstance(value, str) and value.strip():
                return value.strip(), source
        except OSError:
            continue
    return "", "none"


def configured_key(env_name: str = "DEEPSEEK_API_KEY") -> tuple[str, str]:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value, "process_environment"
    return _windows_key() if env_name == "DEEPSEEK_API_KEY" else _windows_key(env_name)


def credential_status(env_name: str = "DEEPSEEK_API_KEY") -> dict:
    value, source = configured_key(env_name)
    return {"available": bool(value), "source": source}


def resolve_key(settings: HarnessSettings, supplied: str | None, *, ops=None) -> str:
    key = supplied.strip() if supplied and supplied.strip() else ""
    if not key and ops is not None:
        key = ops._saved_provider_api_key(settings.model_dump()) or ""
    if not key:
        key = configured_key(settings.key_env)[0]
    if not key and urlsplit(settings.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"请在密码框输入密钥，或为所选服务设置 {settings.key_env}")
    return key or "local-no-auth"


def harness_env(ops) -> tuple[HarnessSettings, str, Path]:
    """模型设置、密钥、工作目录。

    工具面、界面和常驻协作者都从这里拿，免得三处各自读环境变量、各自判超时。
    timeout_seconds仅限制网络连接和流空闲，不是任务总时限。
    """
    settings = HarnessSettings.model_validate(ops.harness_settings())
    api_key = resolve_key(settings, None, ops=ops)
    from .project_package import layout

    return settings, api_key, layout(ops.path).extraction


class HarnessRunner:
    def __init__(self, settings: HarnessSettings, api_key: str, directory: Path,
                 *, task_tools: dict | None = None, system_prompt: str | None = None):
        from deepseek_harness import DeepSeekHarness

        if any(
            version(package) != SDK_VERSION
            for package in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin")
        ):
            raise ValueError(f"需要 deepseek-harness-sdk/runtime=={SDK_VERSION}，请同步项目依赖")
        self.settings = settings
        self._task_tools = task_tools
        self._system_prompt = system_prompt
        self._secret = api_key
        self._close_lock = threading.Lock()
        self._closed = False
        self.active_session = None
        self.turn_settled = True
        self.interrupt_barrier: threading.Event | None = None
        self._compaction_events: list[dict] = []
        self._usage_listener = None
        self._steering_listener = None
        self._live_contexts: dict[str, dict] = {}
        directory.mkdir(parents=True, exist_ok=True)
        self._gateway = ModelGateway(
            settings, api_key, secrets_to_redact=(str((task_tools or {}).get("token") or ""),),
        )
        try:
            self._initialize_harness(DeepSeekHarness, directory)
        except Exception:
            self._gateway.close()
            raise

    def _initialize_harness(self, harness_type, directory: Path):
        settings = self.settings
        binary, runtime_env = project_runtime(directory)
        patch = runtime_patch(settings)
        if self._task_tools is not None:
            tool_config = {key: value for key, value in self._task_tools.items() if key != "token"}
            patch.append({"insert": [{
                "id": "narrative-task-tools",
                "name": str(Path(__file__).with_name("task_tools.mjs").resolve()),
                "config": tool_config,
            }]})
            runtime_env["NARRATIVE_TASK_TOOLS_TOKEN"] = self._task_tools["token"]
        patch_path = directory / "context.patch.json"
        patch_path.write_text(
            json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._harness = harness_type(
            dsh_bin=str(binary),
            dsh_home=str(directory / "home"),
            cwd=str(directory),
            runtime_cwd=str(directory),
            profile="sdk-minimal",
            patches=(
                str(Path(__file__).with_name("extraction.patch.yml")),
                str(patch_path.resolve()),
            ),
            provider="deepseek-official",
            model=settings.model,
            base_url=self._gateway.base_url,
            api_key=self._gateway.token,
            # 运行时只认 low/medium/high。off / minimal 这类档它不认，由网关
            # 自己写进请求体（off 是 thinking 开关，minimal 原样透传给供应商）。
            reasoning_effort=(
                settings.reasoning_effort
                if str(settings.reasoning_effort or "") in RUNTIME_EFFORTS
                else None
            ),
            max_tokens=settings.max_tokens,
            initialize_timeout_seconds=30,
            request_timeout_seconds=None,
            shutdown_timeout_seconds=1,
            env={
                **runtime_env,
                "DSH_SYSTEM_PROMPT": self._system_prompt if self._system_prompt is not None else CONVERSATION_SYSTEM_PROMPT,
            },
        )

    def resume(self, session_id: str) -> bool:
        """让运行时把这会话的上下文从盘上捡回来。

        不认这个号（比如换了机器、运行时清过）就返回 False，让调用方从空会话开始——
        我们自己的对话记录还在，人看到的东西不会丢。
        """
        if self._closed:
            raise ValueError("任务已经停止")
        try:
            self._harness.start()
            if self._closed:
                self.close(force=True)
                raise ValueError("任务已经停止")
            self._harness.client.resume_session(session_id)
            return True
        except Exception:
            if self._closed:
                raise
            return False

    def interrupt_upstream(self):
        self._gateway.cancel_active_turn()

    def close(self, *, force: bool = False):
        with self._close_lock:
            if force or not self._closed:
                self._closed = True
                try:
                    self._gateway.close()
                finally:
                    self._harness.close()

    def redact(self, message: str) -> str:
        return message.replace(self._secret, "[REDACTED]") if self._secret else message

    def usage_metadata(self) -> dict:
        return {
            "usage": self._gateway.usage_snapshot() or None,
            "usage_source": "provider-stream",
            "usage_incomplete": self._gateway.usage_incomplete(),
            "gateway_upstream_attempt_count": len(self._gateway.usage_snapshot()),
            "provider": self.settings.provider,
            "model": self.settings.model,
            "context_policy": effective_runtime_config(self.settings),
            "compaction_events": list(self._compaction_events),
            "model_requests": self._gateway.request_snapshot(),
            "compaction_request_count": len(self._gateway.compaction_snapshot()),
            **self._gateway.compaction_failure_snapshot(),
        }

    @contextmanager
    def usage_events(self, callback):
        """Observe the active real SDK turn without polling logs or raw text."""
        previous = self._usage_listener
        self._usage_listener = callback
        try:
            yield
        finally:
            self._usage_listener = previous

    def _publish_usage_progress(self, session_id, event):
        if self._usage_listener is None:
            return
        context = self._live_contexts.setdefault(session_id, {
            "latest_input_tokens": None, "latest_input_source": "unknown",
            "current_estimate_tokens": None, "current_estimate_source": "unknown",
            "state": "waiting", "stale": True,
            "context_window": self.settings.context_window,
            "compaction_threshold_tokens": self.settings.compaction_threshold,
        })
        kind = event.get("type", "turn/start")
        if kind == "request/context":
            window = (event.get("data") or {}).get("contextWindow")
            if type(window) is int and window > 0:
                context["context_window"] = window
        if kind == "compaction/start":
            context["state"] = "compacting"
        elif kind in {"turn/start", "step/start"}:
            context["state"] = "waiting"
        else:
            context["state"] = "stale"
        # The installed SDK exposes no public Python tokenMeter projection.
        # Its request usage is actual, but output/tools and compaction already
        # changed the surface. Do not invent a current-size estimate from a sum.
        context.update(stale=True, current_estimate_tokens=None, current_estimate_source="unknown")
        requests = self._gateway.usage_progress_snapshot()
        latest = next((row.get("usage") for row in reversed(requests)
                       if row["purpose"] == "generation" and isinstance(row.get("usage"), dict)), None)
        if isinstance(latest, dict) and type(latest.get("prompt_tokens")) is int:
            context.update(latest_input_tokens=latest["prompt_tokens"], latest_input_source="provider_last_request")
        self._usage_listener({
            "provider": self.settings.provider, "model": self.settings.model,
            "context_policy": effective_runtime_config(self.settings),
            "model_requests": requests, "native_context": dict(context),
            "compaction_request_count": sum(row["purpose"] == "compaction" for row in requests),
        }, kind.replace("/", "_"))

    def partial_raw(self) -> list[str]:
        """Provider content observed before an interrupted SDK turn, if any."""
        return self._gateway.raw_snapshot()

    def upstream_activity_snapshot(self) -> dict:
        """ModelGateway last upstream response-byte activity for live progress."""
        return self._gateway.upstream_activity_snapshot()

    def set_steering_listener(self, callback):
        self._steering_listener = callback

    def set_tool_allowlist(self, provider):
        """Filter provider-visible tools per task without rebuilding SDK history."""
        self._gateway.tool_allowlist_provider = provider

    def agent_turn(self, instruction: str, session_id: str, *, on_message=None) -> HarnessReply:
        """Run one agent turn in a persistent SDK session; the caller dispatches safe tools."""
        self.active_session = session_id
        self.turn_settled = False
        try:
            with self._gateway.session(session_id):
                try:
                    return self._run(instruction, session_id, on_message=on_message)
                finally:
                    # The SDK call is already settled. Publish that fact before
                    # waiting for a concurrent stop controller: it may itself be
                    # waiting for this turn to cease being active.
                    self.turn_settled = True
                    self.active_session = None
                    if self.interrupt_barrier is not None:
                        self.interrupt_barrier.wait()
        finally:
            self.active_session = None

    def _run(self, instruction: str, session_id: str, *, on_message=None) -> HarnessReply:
        if self._closed:
            raise ValueError("提取已经停止")
        started = time.monotonic()
        self._compaction_events = []
        model_messages: list[dict] = []
        seen_messages: set[str] = set()

        def observe_message(event):
            if event.get("type") != "assistant/message":
                return
            seq = event.get("seq")
            if seq is None:
                return
            message_id = f"{session_id}:{seq}"
            if message_id in seen_messages:
                return
            data = event.get("data")
            if not isinstance(data, dict):
                return
            message = data.get("message")
            owner = message if isinstance(message, dict) else data
            blocks = owner.get("content")
            if not isinstance(blocks, list):
                return
            visible = [block for block in blocks if isinstance(block, dict)
                       and block.get("type") not in {"reasoning", "thinking", "redacted_thinking"}]
            if not visible:
                return
            from .assistant_audit import sanitize_host_audit
            visible = sanitize_host_audit(json.loads(self.redact(json.dumps(visible, ensure_ascii=False))))
            text = "".join(str(block.get("text") or "") for block in visible if block.get("type") == "text")
            record = {"message_id": message_id, "session_id": session_id, "seq": seq,
                      "text": text, "content": visible,
                      "full_output": json.dumps(visible, ensure_ascii=False)}
            from .output_presentation import present_message
            record = present_message(record)
            seen_messages.add(message_id)
            model_messages.append(record)
            if on_message is not None:
                on_message(record)

        def observe(notification):
            self._observe_compaction(notification)
            if (notification.method == "session.event"
                    and notification.payload.get("sessionId") == session_id):
                event = notification.payload.get("event")
                if isinstance(event, dict):
                    if event.get("type") == "user/message" and self._steering_listener is not None:
                        self._steering_listener(session_id, (event.get("data") or {}).get("id"))
                    if event.get("type") in {"turn/start", "step/start", "request/context", "assistant/message",
                                              "tool/result", "compaction/start", "compaction/end", "turn/end"}:
                        self._publish_usage_progress(session_id, event)
                    observe_message(event)

        from deepseek_harness import Session

        self._harness.start()
        if self._closed:
            self.close(force=True)
            raise ValueError("任务已经停止")
        # request_timeout_seconds=None lets this RPC wait until completion or
        # explicit cancellation. Socket/stream idle limits remain independent.
        result = Session(self._harness, session_id).run(
            instruction, on_notification=observe
        )
        self.turn_settled = True
        # Preserve only server-reported usage. Missing usage remains unknown.
        usage = []
        for event in result.events:
            observe_message(event)
            data = event.get("data")
            if isinstance(data, dict) and data.get("usage") is not None:
                usage.append(data["usage"])
        return HarnessReply(
            result.final_response,
            {
                "sdk": "deepseek-harness-sdk",
                "sdk_version": SDK_VERSION,
                "provider": self.settings.provider,
                "protocol": "chat-completions",
                "service_error": self._gateway.error,
                "sdk_error_diagnostics": _sdk_error_diagnostics(
                    result.events, (self._secret, self._gateway.token)
                ),
                "session_id": result.session_id,
                "model_messages": model_messages,
                "finish_reason": "error"
                if self._gateway.compaction_failure_snapshot()["compaction_incomplete"]
                else result.finish_reason,
                "duration_seconds": round(time.monotonic() - started, 3),
                **self.usage_metadata(),
                "usage": self._gateway.usage_snapshot() or usage or None,
            },
        )

    def _observe_compaction(self, notification):
        if notification.method != "session.event":
            return
        event = notification.payload.get("event")
        if not isinstance(event, dict) or not str(event.get("type", "")).startswith("compaction/"):
            return
        data = event.get("data")
        if not isinstance(data, dict):
            return
        safe = {
            key: data[key]
            for key in (
                "compactionId",
                "turn",
                "shadowedRange",
                "shadowedSeqs",
                "shadowedTokenCount",
                "provider",
                "model",
                "maxTokens",
                "usage",
            )
            if key in data
        }
        if data.get("error") is not None:
            safe["error"] = self.redact(str(data["error"]))[:1000]
            self._gateway.fail_compaction(safe["error"])
        self._compaction_events.append(
            {"type": event["type"], "seq": event.get("seq"), "data": safe}
        )
