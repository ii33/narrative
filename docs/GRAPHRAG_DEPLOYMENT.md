# Microsoft GraphRAG 部署与运行

状态日期：2026-10-20  
适用版本：`graphrag==3.3.0`、`qwen3.8:iq3-mtp3`、`bge-m3`、Ollama `127.0.0.1:11434`

GraphRAG 是**独立 sidecar**：它生成实体、关系、claims、社区报告和向量，只作为检索提示；正式叙事图仍由 `Operations` 审核后写入，GraphRAG 结果不会自动成为事实。

## 1. 环境

### 1.1 目录与依赖

| 项 | 值 |
| --- | --- |
| 独立环境 | `.venv-graphrag` |
| 依赖锁定 | `requirements-graphrag.lock.txt`（146 个包，含 `graphrag==3.3.0`） |
| 创建／修复 | `scripts/setup_graphrag_env.ps1` |
| 主环境 | `.venv`（`uv sync --dev`） |

```powershell
./scripts/setup_graphrag_env.ps1
```

脚本会创建环境、按锁文件安装、在 Windows 上修正完整性级别，并验证 `graphrag` 版本；不会下载模型、不会启动服务、不会写入密钥。

### 1.2 Windows 低完整性目录的坑

工作区如果处于 **Low 完整性**，CPython 的 `venvlauncher` 会报：

```text
failed to open '...\pyvenv.cfg': ...
exit 107
```

这不是 venv 损坏，也不是缺少依赖。修复：

```powershell
icacls ".venv" /setintegritylevel "(OI)(CI)Medium" /T
icacls ".venv-graphrag" /setintegritylevel "(OI)(CI)Medium" /T
```

或直接运行：

```powershell
./scripts/fix_venv_integrity.ps1
```

`setup_graphrag_env.ps1` 已包含对 `.venv-graphrag` 的处理；`.venv` 需要在同一工作区单独执行一次。修复后 `uv run python -V`、`uv run pytest`、`graphrag.exe` 均恢复正常。

### 1.3 模型

```powershell
ollama list
# 需要：qwen3.8:iq3-mtp3、bge-m3:latest
```

## 2. 自检

```powershell
.\.venv-graphrag\Scripts\python.exe scripts\graphrag_env_check.py `
  --root data\ms-graphrag-threechapters-20261018\standard-run
```

检查解释器、`graphrag` 版本、settings 占位符、七张 parquet、三个 LanceDB 表、Ollama 模型和提示词数量。全部通过时输出 `ready: True`。

当前三章索引自检结果：`ready: True`；3 documents / 15 text units / 82 entities / 135 relationships / 23 communities / 23 reports / 113 claims；LanceDB 82 / 23 / 15。

## 3. 建索引

现有三章索引的完整流程（每一步都保留原始响应与用量）：

```powershell
# 1. 暂存来源（当前脚本要求恰好三章；通用暂存可调用 GraphOperations.graphrag_stage_sources）
uv run python scripts/graphrag_stage_chapters.py <source.txt> <manifest.json> <project.sqlite3> <run_root>

# 2. 初始化官方配置
.\.venv-graphrag\Scripts\graphrag.exe init --root <run_root> --model qwen3.8:iq3-mtp3 --embedding bge-m3

# 3. 写入本地模型、提示词哈希和并发限制（必须用 graphrag 环境解释器，依赖 pyyaml）
.\.venv-graphrag\Scripts\python.exe scripts\graphrag_configure.py <run_root>

# 4. 预检（不调用模型）
.\.venv-graphrag\Scripts\python.exe scripts\graphrag_bounded_run.py <run_root> --preflight

# 5. 有界索引（默认 11 请求 / 250k token；三章最终运行曾按用户授权移除限制）
.\.venv-graphrag\Scripts\python.exe scripts\graphrag_bounded_run.py <run_root> --max-requests 11

# 6. 导出 parquet 为预览 JSON
uv run python scripts/graphrag_export.py <run_root> <export.json>
```

第 6 步之后，用 `Operations.graphrag_preview_staged(run_root, export_json)` 做只读预览；正式图修订不会改变。

**冒烟验证（2026-10-20）**：用 17 字合成文本跑通 init → configure → preflight → bounded index，`index_exit_code=0`、4 次请求、7,956 tokens、`required_outputs_present=true`；产物为 1 document / 1 text unit / 3 entities / 3 relationships / 1 community / 1 report。目录：`data/graphrag-init-smoke-20261020/`。

## 4. 查询

```powershell
uv run python scripts/graphrag_query.py `
  --root data\ms-graphrag-threechapters-20261018\standard-run `
  --method local --query "林舟为什么被传送到火星？"
```

支持的 `--method`：`local`、`global`、`basic`、`drift`。脚本会：

- 设置 `GRAPHRAG_API_BASE`（默认 `http://127.0.0.1:11434/v1`）和本地 key；
- 设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，避免离线环境下拉取远程价格表超时；
- 把答案写入 `narrative-graphrag-query-<method>-<时间>.md`；
- 把退出码、耗时、输出哈希、stderr 尾部和 `logs/query.log` 的指标块写入同名 `.json`；
- 不重试，不打印密钥。

## 5. 已验证的查询结果

三条查询都在三章索引上实跑，**顺序执行**；首次 local 与 basic 曾并发，未作为性能基线。
以下是**优化前**（官方英文提示词、12k 上下文）的基线；中文提示词与 20k 上下文的结果见 5.1。

| 方法 | 耗时 | 模型请求 | Token | 输出 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| local | 27.9 s | 1 次补全 + 1 次嵌入 | 补全未返回 usage | 1,571 字 | 正确回答林舟被传送的原因，带 Entities/Sources 引用 |
| basic | 59.8 s | 3 次补全 + 1 次嵌入 | 28,344 | 865 字 | 正确解释火纹石，带 Sources 引用 |
| global | 76.2 s | 3 次补全 | 28,344 | 2,340 字 | 给出人物关系与核心冲突，带 Reports 引用 |

三条查询均 `exit_code=0`，均未写入正式图。local 和 basic 日志出现
`Reached token limit ... reverting to previous context state`，说明上下文在预算内被截断后仍继续回答；这不是完整召回。

## 5.1 查询侧优化与回归套件（2026-10-20）

本轮只改**查询侧**，索引、缓存和向量未重建。配置记录在
`narrative-graphrag-query-profile.json`，旧配置备份为
`settings-before-query-optimization-20261020.yaml`。

| 项 | 优化前 | 优化后 |
| --- | --- | --- |
| local / basic 提示词 | 官方英文 | `prompts-zh/` 中文提示词 + 正名表 |
| local / basic 上下文 | 12,000 tokens | 20,000 tokens |
| global map | 官方英文 | **保留官方英文**（JSON 更稳定） |
| global reduce | 官方英文 | 中文 reduce + 正名表 |
| global 上下文 | 12,000 / 12,000 | 20,000 / 20,000 |

正名表覆盖林舟、老周、苏娘、陈默、地球、火星、云顶星、落霞星、火纹石、铜钥匙、寒铁剑、玄石派、镜湖等；所有写法先从三章原文核对，目标是消除 `Lin Zhou → 林舟`、`苏娘 → 苏娘` 这类音译漂移。

### 回归结果

`scripts/graphrag_query_suite.py` 用固定四题跑 local / basic / global，检查退出码、非空、必含词、禁用词（`林舟`）和 `[Data: ...]` 引用：

| id | 方法 | 结果 | 耗时 | 引用数 |
| --- | --- | --- | ---: | ---: |
| transfer-local | local | pass | 49.2 s | 6 |
| zhiyanxin-basic | basic | pass | 28.6 s | 6 |
| relations-global | global | pass | 92.1 s | 11 |
| hua-local | local | pass | 36.5 s | 5 |

套件 **4/4 通过**，结果在 `query-suite-20261019T185340Z/`。正名修正后 `hua-local` 中“苏娘”出现 7 次、“苏娘”0 次；global 不再出现“林舟”，basic 不再触发 token limit 警告；local 仍会因 covariates 预算触发警告。耗时受 GPU 预热和上下文长度影响，不能当作性能基线。

### drift 测试失败

`--method drift` 对“林舟与老周的关系如何演变？”共尝试三种模型：

| 模型 | 上下文 | 结果 | 耗时 | 说明 |
| --- | ---: | --- | ---: | --- |
| `qwen3.8:iq3-mtp3` | 32k | exit 1，0 字 | 226.8 s | `PrimerResponse` 结构不满足；5 请求 / 26,341 tokens |
| `qwen3.8:128k`（Q3_K_L） | 128k | 超时，0 字 | 600 s | 10 分钟只完成 2/5 个 primer 步骤，估算需 19 分钟 |
| `qwen3.8:iq3-128k` | 128k | exit 1，0 字 | 352.2 s | 同样的 `PrimerResponse` 结构失败；4 请求 / 20,093 tokens |

结论：**上下文长度不是瓶颈，结构化输出遵循才是**。`qwen3.8:128k` 的 Q3_K_L 权重在本机 16GB 显存上过慢，不适合交互查询；`iq3-128k` 速度可接受但仍不满足 drift 的 JSON 结构。drift 在本机**暂不可用**，没有重试或用其它结果冒充。65k 与 128k 同为 Q3_K_L 14.07GB，未重复测试。

### 仍存在的术语漂移

正名表解决了主要人名，但答案里仍会出现 `紫焰心/火纹石`、`塞湖/镜湖`、`玄石派/玄石派` 等变体。**正名以原文为准**：苏娘、火纹石、镜湖、玄石派、云顶星、寒铁剑；不是模型译名。根因在索引侧的英文提示词与罗马化实体标题；要根治需要重建索引并改用中文抽取提示词，成本较高，本轮未做。

## 5.2 中文索引重建（2026-10-21）

新 root：`data/ms-graphrag-threechapters-zh-20261020/`。索引侧提示词改为简体中文输出、实体名保持原文中文写法、claims 主体/客体中文、社区报告文本字段中文；实体类型改为中文。分五次尝试：

| 尝试 | 改动 | 结果 |
| ---: | --- | --- |
| 1 | 中文提示词，输出上限 5000 | 73 请求 / 160,486 tokens；第 73 次 claims `finish_reason=length`，网关停机，社区报告 `KeyError` |
| 2 | 输出上限 10000 | 参数校验失败：网关 `MAX_OUTPUT_TOKENS=6000` 拒绝 |
| 3 | 网关上限 12000 | 8 请求 / 43,455 tokens；claims 仍以 10,000 tokens 截断 |
| 4 | claims 限制 20 条/4000 字 | 17 请求 / 78,131 tokens；社区报告仍以 10,000 tokens 截断 |
| 5 | 社区报告限制 5 findings/3000 字 | **18 请求 / 87,746 tokens / exit 0 / complete** |

最终产物：3 documents / 15 text units / **75 entities** / **114 relationships** / **17 communities** / **17 reports** / **118 claims**。

### 与英文索引对比

| 指标 | 英文索引 | 中文索引 |
| --- | ---: | ---: |
| 实体标题为中文 | 0 / 82 | **75 / 75** |
| 社区报告语言 | 英文 | 中文（摘要汉字占比 87.6%） |
| claims 精确引文 | 41 / 113（36.3%） | **60 / 118（50.8%）** |
| 查询套件 | 4/4 | **4/4** |
| 查询引用数 | 6 / 6 / 11 / 5 | **12 / 11 / 29 / 11** |
| 实体 / 关系 / 社区 | 82 / 135 / 23 | 75 / 114 / 17 |

中文索引的实体、关系、社区数量略少，可能是中文提示词加了输出限制后模型收敛得更紧；这不等于质量更好或更差，需要按查询正确率评估。查询套件四题全部通过，引用数明显增加。

### 中文索引上的交叉审核

| 指标 | 英文索引 | 中文索引 |
| --- | ---: | ---: |
| matched | 32 | 31 |
| unit_only | 20 | 20 |
| missing | 8 | 9 |
| no_evidence | 2 | 2 |
| 涉及节点的 claims | 110 | 115 |
| SUSPECTED claims | 5 | 20 |

实体覆盖基本持平；中文索引的 claims 更多、`SUSPECTED` 也更多，说明 claims 召回提高但噪声同步增加。事件覆盖仍然弱：15 条 unit_only、8 条 missing，GraphRAG 的事件粒度无法逐事件对齐。

## 6. 已知限制

1. **中文质量（已改善，未根治）**：中文索引 75/75 实体标题为中文，claims 精确引文 60/118（50.8%）；英文索引仍是 0/82 中文标题。查询侧已加正名表，但术语漂移和近一半 claims 不能精确定位仍是限制。
2. **claims 定位率**：英文索引 113 条 claims 只有 41 条能精确匹配原文（36.3%），中文索引 118 条中 60 条（50.8%）；其余是释义或未定位。
3. **无增量索引**：换书或改文本需从头建；`graphrag_bounded_run.py` 默认 11 请求，长文本需显式授权提高上限。
4. **无 UI/CLI 入口**：只有 `Operations.graphrag_stage_sources` / `graphrag_preview(_staged)` 两个只读边界，索引仍需手工按第 3 节执行。
5. **上下文截断**：local/basic 会触发 token limit 回退；答案可能遗漏上下文，不能当作完整召回。
6. **drift 不可用**：已实测一次，模型无法满足 `PrimerResponse` 结构化输出，exit 1、0 字输出；其余三种方法可用。
7. **不是正式事实**：GraphRAG 的实体、关系、claims 和报告都只是检索提示；正式图修订在查询后保持不变。

## 7. 相关文件

- `docs/MS_GRAPHRAG_STANDARD.md`：三章标准索引的构建、恢复与验收记录。
- `src/narrative/ms_graphrag_adapter.py`：只读预览适配器。
- `src/narrative/graph_operations.py`：`graphrag_stage_sources`、`graphrag_preview_staged`。
- `scripts/graphrag_env_check.py`、`scripts/graphrag_query.py`、`scripts/graphrag_query_suite.py`、`scripts/graphrag_query_suite.json`、`scripts/setup_graphrag_env.ps1`：本次补齐的部署与测试工具。
