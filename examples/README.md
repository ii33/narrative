# 分叉会合事件线演示

`branch_merge.json` 是原创合成原文，不含用户作品。E0 后分成 A1→A2→A3 与 B1→B2，
两线在 E9 会合。C1→C2 是独立事件线。A2 与 B1 的先后未知；A2→B2 仅留作待审候选，
正式图仍显示 `unknown`。

在仓库根目录用 PowerShell 创建隔离的持久演示库：

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe examples\build_branch_merge.py
```

脚本仅使用 `Operations` 导入合成原文、提出并人工接受示例中的八条时序断言。
默认输出为 `data/graph-projects/branch-merge-demo.sqlite3`，同时生成同目录的
`branch-merge-demo-manifest.json`，记录待审候选和偏序比较。已有同名库时脚本会停止，
不会覆盖它。

打开原生可交互界面：

```powershell
.\.venv\Scripts\python.exe -m narrative.native_app --projects-dir data\graph-projects --project branch-merge-demo
```

进入“时间轴”，用“事件比较”选择 A2 与 B1，应显示未知；关闭比较面板可查看
两条会合支线和独立的 C 线。CLI 亦可查询：

```powershell
.\.venv\Scripts\python.exe -m narrative.cli --db data\graph-projects\branch-merge-demo.sqlite3 graph-event-compare A2 B1
```

## Node property state and event changes

`property_story.json` documents an initial holder, a successful transfer, a rejected stale transfer, a flashback observation, unordered branch updates, and a reviewed retraction. The observation records what Mira held at its anchor; it does not invent an acquisition event. `build_property_story.py` stages and accepts only reviewed graph/property candidates through `Operations`, records the failed transfer diagnostics, and saves a manifest with state/history query results.

Build the persistent demo from the repository root:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
.\.venv\Scripts\python.exe examples\build_property_story.py
```

The default database is `data/graph-projects/property-story-demo.sqlite3`. The script stops if either the database or manifest already exists; pass `--output` for a different new path. The manifest reports that the stale transfer leaves Jon as holder, unordered branch changes produce a conflict, and retracting branch B rebuilds the projection while preserving history.

Open the persistent demo in the native Qt window from the repository root:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m narrative.native_app --projects-dir data\graph-projects --project property-story-demo
```

Select **节点状态**, enter node `key` and property `holder`, then click **查询状态与历史**. The page shows the current state, accepted history, and the aggregate world projection. Use the `at` field for a JSON story TimeRef, such as `{"timeline_id":"story","form":"event_boundary","event_id":"flashback","boundary":"start"}`.
