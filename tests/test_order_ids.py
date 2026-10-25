"""Order ids: segment-wise integers, insertable without renumbering."""

from __future__ import annotations

from narrative.order_ids import allocate, order_key


def test_segments_compare_as_integers() -> None:
    values = ["1", "1.1", "1.1.1", "1.2", "1.10", "2"]
    assert sorted(values, key=order_key) == values
    # A shorter id comes first when its segments all match.
    assert order_key("1.2") < order_key("1.2.1") < order_key("1.3")


def test_invalid_forms_are_rejected() -> None:
    assert order_key("1.0") is None          # a trailing zero leaves no room
    assert order_key("1.01") is None         # leading zeros would duplicate 1.1
    assert order_key("ex_abc") is None
    assert order_key("") is None
    assert order_key("1.0.1") == (1, 0, 1)   # interior zero is fine


def test_insertion_takes_a_new_segment() -> None:
    taken = {"1", "2"}
    existing = [((1, 0), "1"), ((1, 100), "2")]
    # "prev.0.k" is the only shape that lands strictly between prev and next.
    assert allocate(existing, (1, 50), taken) == "1.0.1"
    assert allocate(existing, (1, 200), taken) == "3"
    assert allocate(existing, (0, 0), taken) == "0.1"


def test_taken_ids_are_skipped() -> None:
    """When the natural slot is taken, add a segment rather than renumber."""
    taken = {"1", "1.1", "1.0.1"}
    existing = [((1, 0), "1"), ((1, 100), "1.1")]
    new_id = allocate(existing, (1, 50), taken)
    assert new_id == "1.0.2"
    assert order_key("1") < order_key(new_id) < order_key("1.1")


def test_empty_project_starts_at_one() -> None:
    assert allocate([], (1, 0), set()) == "1"
