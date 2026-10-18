# 复用记录

核验日期：2026-10-16。业务代码独立实现；通过依赖复用 DeepSeek Harness SDK／运行时，未复制上游源码或提示词。锁定版本及分发文件哈希见 `uv.lock`。

| 来源与固定版本 | 实际方式／范围 | 许可与边界 | 本地证据与退出路径 |
|---|---|---|---|
| [Narra](https://github.com/florinutz/narra/tree/1225a962b9313f96f30f59b2849d763f71c9d068)，`src/models/knowledge.rs`，blob `b5a20c048c92e69db1a98c45dffb54102a5ca844` | 仅设计参考：确定程度、获得方式、来源人物、稳定实体引用 | 上游计划记录 MIT；本轮未复制代码，不引入 Rust／SurrealDB；不采用默认已知／初始知识 | `test_exact_unicode_duplicate_and_unknown`、`test_claim_stays_claim_after_restart`、`test_character_reference_survives_entity_edit`；本地 Pydantic 对象可独立维护 |
| [DNE](https://github.com/Yasshu55/Dynamic-Narrative-Engine/tree/77faf2ae14e9421d0366ca87f3a08e6cefbeeb22)，`apps/api/ingestion/entity_extractor.py`，blob `ffb5a2824c5e185914564a0a5963237c1511a904` | 仅流程参考：结构化提取与候选审核；提示词和解析独立实现 | README 的 MIT 声明不视为代码分发准入；独立 LICENSE 材料尚未核实。不复制提示词、不迁入默认存活／active、不截取前 80,000 字符 | `test_long_text_full_coverage_and_tail`、`test_ambiguous_result_failed_then_explicit_location`；不依赖 DNE 运行即可手工整理 |
| [coating-sim](https://github.com/ii33/coating-sim/tree/f957096afacd666ac99ccc23fc78cc8546881a3d) | 已读取 `agent/modelclient.py`、`provider_protocol.py`、`model_compat.py` 和 runtime 接入部分，作为模型预算与协议边界参考 | MIT；源码现已公开。按后续用户决定，不迁移其完整 Pydantic AI／MCP／领域运行时，也未复制源码 | 统一 `Operations` 接口继续保留；无需薄膜领域依赖 |
| [DeepSeek Harness Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)，PyPI `deepseek-harness-sdk==0.1.5rc1`、`deepseek-harness-runtime-bin==0.1.5rc1` | 直接依赖发布包；实际 API 以安装 wheel 的 `DeepSeekHarness`／`Session` 为准；`harness.py` 薄适配，`jobs.py` 管本地任务，操作层统一调用 | 上游 MIT；版本与 wheel 哈希锁定在 `uv.lock`，未修改上游库。自编专用 profile patch 禁用 shell、日志上传和重试，不迁入 DSH UI | `tests/test_harness.py` 使用真实 Windows 随包运行时与本地模拟服务，覆盖候选审核、错误、截断、取消、超时、重复启动和中断恢复；火山方舟真实短句提取已生成可定位候选；整章曾在 high 推理强度下超时，当前项目已改用 low。退出时继续使用原有手工工作台和离线 JSON 交接 |

复用准入：实际复制前核查固定提交、具体文件的授权、版权和 NOTICE；复制后另记录修改。未把候选参考、模拟响应、默认分支文件或文件 blob SHA 当作已验证的完整集成版本。
