# Microsoft GraphRAG standard sidecar

The project runs Microsoft's `graphrag==3.3.0` from an isolated
`.venv-graphrag`. It keeps generated entities, relationships, claims,
communities, reports and dense vectors separate from the reviewed narrative
graph. UI, CLI and harness business actions still enter through Operations.

## Local three-chapter configuration

The first three chapters were imported through `GraphOperations` into the
isolated project `data/ms-graphrag-threechapters-20261018/project.sqlite3` and
staged under `data/ms-graphrag-threechapters-20261018/standard-run/input`.
`narrative-source-map.json` records three source IDs and SHA256 hashes. The
original combined sample has SHA256
`ac55312929b03f2dbf111f9862d06b5200764bc00e5cc3ddbb3c047cf5bcc4c3`.
The staged files contain 16,106 characters, including 13,221 Han characters.
They remain under ignored `data`; no original attachment or previous report
was overwritten.

The official `graphrag init` root uses the locally installed Ollama
`qwen3.8:iq3-mtp3` for completion (Qwen 27.3B, IQ3_XXS, configured 32K
context) and `bge-m3` for 1,024-dimensional dense embeddings. Their pinned
GGUF model blob SHA256 values are, respectively,
`c0b7c3038681ed2e3040456c1dd45f9858b6c2290bed172c70388a94874f3eee`
and `daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c`.
The generation endpoint is `http://127.0.0.1:11434/v1`; Ollama reported its
runner as `llamacpp`. The model has `draft_num_predict=3` in its manifest,
which alone does not prove speculative decoding was used. No model was
downloaded or sent to a cloud endpoint in this phase.

The settings use `${GRAPHRAG_API_BASE}` and `${GRAPHRAG_API_KEY}` placeholders,
which the localhost monitoring gateway supplies to the official CLI. Direct
indexing without those variables fails configuration; a manually supplied
environment could bypass the gateway. The configuration has one concurrent
request, no GraphRAG or LiteLLM retry, zero extraction gleanings, claims
enabled and 13 hashed stock prompt files. Graph and description stages use a
5,000-token completion limit. Claims and community reports use 6,000 tokens
with `reasoning_effort=none` injected by the local gateway. A synthetic
Pydantic community-report probe confirmed valid structured output and zero
reasoning characters through that path. BGE-M3 is local dense retrieval only;
GraphRAG does not use the existing RAG's sparse or ColBERT channels.

## Index outcome and recovery

The final repaired official `standard` run exited 0. It created 15 text units
totaling 15,242 tokens with overlap; 82 entities; 135 relationships; 113
claims; 23 communities; and 23 community reports. LanceDB has 82 entity, 23
community and 15 text-unit vectors. Every final graph, description, claims and
community cache response has `finish_reason=stop`.

The first run stopped at the former 11-request limit. After the user removed
local request and token limits, continuation reused the graph caches. One
claims response later reached the 5,000-token limit because Ollama counted
hidden reasoning within output usage. A direct local call respected
`reasoning_effort=none`, but GraphRAG's LiteLLM path omitted it. The gateway
now injects it only for the dedicated 6,000-token claims/report calls. A
separate LanceDB error came from Rust trying to write to the system Temp;
setting the child process Temp to the isolated run directory fixed it. The
truncated claim cache entry was archived, then just that response was
recomputed. All failed-run reports, raw responses and cache history were
retained. The final run report is `narrative-graphrag-resume-4.json`; the
complete acceptance summary is `narrative-graphrag-final-acceptance-v2.json`.

## Retrieval and review boundary

`scripts/graphrag_export.py` exports the official Parquet tables to
`narrative-standard-export-repaired.json`. The read-only
`GraphOperations.graphrag_preview_staged()` maps all 113 model claims back to
the three source records. `Operations.locate()` exactly matched 41 short
quotes; the remaining 72 claims remain paraphrases or unlocated search hints.
Formal revision stayed 3 and the candidate count stayed zero. All 135
relationships link to text units, which provide chunk provenance rather than
exact quote or reviewed fact status.

Three final BGE-M3 queries found their target entity at rank 1. Their top
five text-unit results overlapped that entity's indexed provenance in 5, 5
and 1 units. The selected model-generated entity titles were romanized, so a
literal title search against Chinese text found zero mentions. This small
check verifies local retrieval and source mapping, not broader answer quality.
Detailed results are in `narrative-graphrag-vector-acceptance-repaired.json`,
`narrative-graphrag-vector-provenance-repaired.json` and
`narrative-graphrag-operations-acceptance-repaired.json` under the index root.

## Environment repair and query validation (2026-10-20)

The isolated environment was repaired after the workspace was found to be at
Low integrity, which made CPython's venv launcher exit 107 with
`failed to open pyvenv.cfg`. Raising `.venv` and `.venv-graphrag` to Medium
integrity fixed both; `scripts/setup_graphrag_env.ps1` now recreates the
GraphRAG environment from `requirements-graphrag.lock.txt` and applies the
same fix. `scripts/graphrag_env_check.py` reports `ready: true` for the
three-chapter index (3/15/82/135/23/23/113 tables, LanceDB 82/23/15).

The configured query modes were exercised for the first time on that index.
`local` returned in 27.9 s with one completion and one embedding call;
`basic` in 59.8 s with three completions (28,344 tokens); `global` in 76.2 s
with three completions (28,344 tokens). All exited 0 and cited
Entities/Sources/Reports. `local` and `basic` logged
`Reached token limit ... reverting to previous context state`, so the answers
are not complete-recall results. The global answer translated `Lin Zhou` as
“林舟” while local used “林舟”, confirming the known romanized-title and
translation-consistency weakness. A 17-character synthetic smoke index also
completed end to end (4 requests, 7,956 tokens, exit 0).

This closes the “query modes configured but never run” gap for one index; it
is not an answer-quality or full-book baseline. Installation, self-check,
index commands and limits are documented in
[GRAPHRAG_DEPLOYMENT.md](GRAPHRAG_DEPLOYMENT.md).

A query-side optimization on the same index added Chinese prompts with a
canonical-name glossary for local/basic and the global reduce step, kept the
stock English global map prompt for reliable JSON, and raised context budgets
to 20,000 tokens. A fixed four-question suite passed 4/4 with 5–13 citations,
and the `林舟` mistranslation disappeared. Drift search was attempted with `qwen3.8:iq3-mtp3` (32k),
`qwen3.8:128k` (Q3_K_L, 128k) and `qwen3.8:iq3-128k` (IQ3_XXS, 128k): the first
and third exited 1 because the model did not satisfy the `PrimerResponse`
schema, while the Q3_K_L variant timed out after 10 minutes at 2/5 primer
steps. Context length is not the blocker; structured-output compliance is.
Drift is recorded as unavailable rather than passed. Residual term drift
(`紫焰心/火纹石`, `塞湖/镜湖`) remains rooted in the English index-time prompts
and romanized entity titles. Canonical names are checked against the source
text: 苏娘, 火纹石, 镜湖, 玄石派, 云顶星, 寒铁剑.

## Usage and extrapolation

The initial index plus four continuations made 100 local chat requests and
used **284,919 input-plus-output tokens**: 164,590 input and 120,329 output,
including one archived truncated response and repeated compatibility checks.
Successful stage calls excluding those extras used 277,389 input-plus-output
tokens. GraphRAG reported 51,387 local BGE-M3 embedding tokens
for the first successful vector build; cache-based rebuilds are not added
again. Ollama did not return separate reasoning-token counts, so completion
usage combines visible output and hidden reasoning. The acceptance summary
contains per-stage input/output usage and request durations.

Dividing this small sample's chat input-plus-output total (284,919) by its
13,221 Han characters gives about 21.6 million chat tokens per million Han
characters (21.0 million excluding recovery overhead), plus embedding work.
This is a mechanical ratio, **not**
a full-book forecast: entity density, community structure, chunk boundaries
and recovery calls are nonlinear. The local model has no token-priced API
charge. The highest manually sampled GPU memory was 15,774 MiB of 16,376
MiB; it is not a continuously measured peak, and CPU/RAM peak was unavailable.

The monitoring gateway saves request hashes, raw responses, usage and finish
reasons, reports activity every 30 seconds, stops on a failed model response
and can be interrupted. The local run has no request or token limit. No Ark
request was made during this integration phase, and no generated sidecar fact
was accepted into the formal narrative graph.
