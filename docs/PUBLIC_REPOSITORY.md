# Public repository scope

This repository and anything built from it contain the tool, not somebody's work.

## What is in here

Application source, tests, and synthetic sample text (`src/narrative/sample.txt`, whose cast —
林舟 / 沈砚 / 铜钥匙 — is invented for the repo). Design and contract documents describe how the
model works, using that same synthetic cast in examples.

## What is never in here

- **No project data.** Graph databases, extraction caches (`*_extraction/`), conversations
  (`*_chats/`), material cards and images, acceptance runs. `data/` is ignored, and the build
  config prunes it, so a release cannot carry it by accident.
- **No real work.** No chapter text, no real character, place or item names, no screenshots of a
  real project. Earlier preview images and run reports were removed for this reason.
- **No saved output from real model runs.** `examples/harness_result.json` and the regression test
  that replays a real response stay out: their provenance is not clear enough to redistribute.

`src/narrative/texts/*.txt` are excluded too. The built-in text catalog and
`tests/test_builtin_texts.py` expect those resources to exist locally; obtain the texts
independently and place them in the expected directory. Do not commit them.

## How this is enforced

- `tests/test_public_baseline.py` fails if a tracked file names a specific project, or if a project
  path is tracked at all. Its marker list is written in fragments so the test does not trip itself.
- `pyproject.toml` keeps the wheel to code (`src/narrative/texts`, databases, caches excluded) and
  lists the sdist contents explicitly instead of walking the tree — which is both slow and how
  project data used to leak into a build.
- `docs/PUBLIC_REPOSITORY.md` (this file) is the intent; the two above are the mechanism.

Historical reports may link to files under `data/` that are not included here; those links will not
resolve in a public checkout. This baseline does not claim the full suite passes without locally
supplied resources.
