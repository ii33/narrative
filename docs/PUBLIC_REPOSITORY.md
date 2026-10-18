# Public repository scope

This public baseline contains the application source, tests, and synthetic sample text. It does not include complete real works, private project databases, private validation materials, or saved output from real model runs.

The files `src/narrative/texts/*.txt` are intentionally excluded. The built-in text catalog and `tests/test_builtin_texts.py` expect those resources to exist locally. To use or test built-in text loading, obtain the texts independently and place them in the expected local resource directory; do not commit them to this repository.

Saved example result JSON files and the regression test that replays a real model response are excluded from the public baseline because their provenance is not sufficiently clear for redistribution. The remaining sample text and event story are synthetic examples.

Historical validation reports may link to files under `data/` or other local result directories that are not included here. Those links may not work in a public checkout. This baseline does not claim that the full test suite runs successfully without locally supplied resources, nor that all tests pass in a clean public checkout.
