"""TASK-03/A: normative pure draft math, staged visibility and strict DTOs."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from adrobot.editor.domain import PreviewProblem, calculate_preview
from adrobot.editor.schemas import PreviewInput
from adrobot.keitaro.read_models import JS_SAFE_INTEGER


def draft_payload(
    *,
    base: list[tuple[int, int]] | None = None,
    known: list[int] | None = None,
    draft_known: list[int] | None = None,
    base_pins: dict[int, int] | None = None,
    active: list[int] | None = None,
    pins: dict[int, int] | None = None,
    mode: str = "SNAPSHOT",
) -> dict[str, Any]:
    """Use numeric IDs for normative A=1, B=2, C=3, X=4 examples."""
    base = [(1, 25), (2, 25)] if base is None else base
    known = [1, 2, 3] if known is None else known
    draft_known = list(known) if draft_known is None else draft_known
    return {
        "base_revision": "opaque-local-test-revision",
        "base_pin_revision": "opaque-local-pin-revision",
        "base_offers": [{"id": item_id, "raw_share": share} for item_id, share in base],
        "base_known_ids": known,
        "draft_known_ids": draft_known,
        "base_pinned_shares": {} if base_pins is None else base_pins,
        "active_ids": [item_id for item_id, _ in base] if active is None else active,
        "pinned_shares": ({} if base_pins is None else dict(base_pins)) if pins is None else pins,
        "mode": mode,
    }


def preview(payload: dict[str, Any]) -> Any:
    return calculate_preview(PreviewInput.model_validate(payload))


def shares(result: Any) -> list[int]:
    active = set(result.active_ids)
    return [item.share for item in result.rows if item.id in active]


def errors(payload: dict[str, Any], code: str) -> None:
    with pytest.raises(PreviewProblem) as exc:
        preview(payload)
    assert exc.value.code == code


def test_snapshot_never_normalizes_partial_or_large_raw() -> None:
    for raw in ((25, 25), (100, 25), (0, 165), (0, JS_SAFE_INTEGER)):
        result = preview(draft_payload(base=list(zip((1, 2), raw, strict=True))))
        assert shares(result) == list(raw)
        assert result.share_unit == "kt_raw"
        assert result.mode == "SNAPSHOT"
        assert result.dirty is False
        assert result.rows[2].status == "Removed"
        assert result.rows[2].unit == "percentage_points"
        assert result.rows[2].share == 0


def test_snapshot_pin_100_with_unpinned_25_and_recalculation_rejection() -> None:
    base = draft_payload(base=[(1, 100), (2, 25)])
    pinned = {**base, "pinned_shares": {1: 100}}
    result = preview(pinned)
    assert (result.mode, result.dirty, shares(result), result.pinned_shares) == (
        "SNAPSHOT",
        True,
        [100, 25],
        {1: 100},
    )
    # Adding C produces a mathematical error, not a retroactive rejection of Pin(A).
    errors(
        {**pinned, "active_ids": [1, 2, 3], "mode": "RECALCULATED"},
        "free_zero_with_unpinned",
    )
    assert preview({**pinned, "pinned_shares": {}}).model_dump() == preview(base).model_dump()

    pinned_both = {**pinned, "pinned_shares": {1: 100, 2: 25}}
    assert shares(preview(pinned_both)) == [100, 25]
    errors({**pinned_both, "mode": "RECALCULATED"}, "pinned_total_exceeds_100")


def test_snapshot_pin_only_no_normalization_ex03() -> None:
    result = preview(draft_payload(pins={1: 25}))
    assert result.mode == "SNAPSHOT" and result.dirty
    assert shares(result) == [25, 25]
    assert result.rows[0].pinned_share == 25
    assert result.rows[1].pinned_share is None


def test_archived_pin_kept_in_full_map_but_not_reserved() -> None:
    base = draft_payload(base_pins={3: 90})
    result = preview(
        {
            **base,
            "active_ids": [1, 2, 4],
            "draft_known_ids": [1, 2, 3, 4],
            "mode": "RECALCULATED",
        }
    )
    assert shares(result) == [34, 33, 33]
    assert result.pinned_shares == {3: 90}
    assert result.rows[2].status == "Removed"
    assert result.rows[2].pinned_share == 90


def test_three_four_five_and_uneven_share_remainders() -> None:
    base = draft_payload(draft_known=[1, 2, 3, 4, 5])
    for active, expected in (
        ([1, 2, 3], [34, 33, 33]),
        ([1, 2, 3, 4], [25] * 4),
        ([1, 2, 3, 4, 5], [20] * 5),
    ):
        result = preview({**base, "active_ids": active, "mode": "RECALCULATED"})
        assert shares(result) == expected
        assert sum(shares(result)) == 100
        assert result.mode == "RECALCULATED" and result.dirty
        assert result.share_unit == "percentage_points"


def test_pin_25_with_two_free_returns_25_38_37() -> None:
    result = preview(draft_payload(active=[1, 2, 3], pins={1: 25}, mode="RECALCULATED"))
    assert shares(result) == [25, 38, 37]


def test_recalculated_results_are_in_active_order_not_row_order() -> None:
    result = preview(draft_payload(active=[3, 2, 1], mode="RECALCULATED"))
    assert result.active_ids == [3, 2, 1]
    assert [(row.id, row.share) for row in result.rows] == [(1, 33), (2, 33), (3, 34)]


def test_ex01_operation_paths_converge_on_same_result() -> None:
    base = draft_payload(base_pins={1: 25})
    # Unpin(A), then Bring Back(C).
    first = preview({**base, "pinned_shares": {}, "mode": "SNAPSHOT"})
    assert shares(first) == [25, 25] and first.dirty
    end_one = preview(
        {**base, "active_ids": [1, 2, 3], "pinned_shares": {}, "mode": "RECALCULATED"}
    )
    # Bring Back(C), then Unpin(A).
    mid = preview({**base, "active_ids": [1, 2, 3], "mode": "RECALCULATED"})
    assert shares(mid) == [25, 38, 37]
    end_two = preview(
        {**base, "active_ids": [1, 2, 3], "pinned_shares": {}, "mode": "RECALCULATED"}
    )
    assert end_one.model_dump() == end_two.model_dump()
    assert (shares(end_one), end_one.pinned_shares, end_one.mode, end_one.dirty) == (
        [34, 33, 33],
        {},
        "RECALCULATED",
        True,
    )


def test_ex02_same_ids_but_changed_pin_keeps_recalculated() -> None:
    base = draft_payload()
    with_c = {**base, "active_ids": [1, 2, 3], "mode": "RECALCULATED"}
    assert shares(preview(with_c)) == [34, 33, 33]
    pinned = {**with_c, "pinned_shares": {1: 34}}
    assert shares(preview(pinned)) == [34, 33, 33]
    final = preview({**pinned, "active_ids": [1, 2]})
    assert (shares(final), final.mode, final.dirty) == ([34, 66], "RECALCULATED", True)


def test_ex04_full_return_to_baseline_restores_snapshot_clean() -> None:
    base = draft_payload()
    expanded = preview({**base, "active_ids": [1, 2, 3], "mode": "RECALCULATED"})
    assert shares(expanded) == [34, 33, 33]
    recovered = preview({**base, "mode": "RECALCULATED"})
    assert (shares(recovered), recovered.mode, recovered.dirty) == ([25, 25], "SNAPSHOT", False)
    assert recovered.model_dump() == preview(base).model_dump()


def test_full_pin_map_and_order_must_match_for_clean() -> None:
    base = draft_payload(base_pins={3: 25})
    assert preview(base).dirty is False
    assert preview({**base, "pinned_shares": {}}).dirty is True
    assert preview({**base, "pinned_shares": {3: 25}, "mode": "RECALCULATED"}).dirty is False
    changed_order = preview({**base, "active_ids": [2, 1], "mode": "RECALCULATED"})
    assert changed_order.dirty is True and changed_order.mode == "RECALCULATED"
    assert changed_order.active_ids == [2, 1]


def test_staged_add_remove_bringback_and_cancel_view() -> None:
    base = draft_payload()
    staged = {
        **base,
        "draft_known_ids": [1, 2, 3, 4],
        "active_ids": [1, 2, 4],
        "mode": "RECALCULATED",
    }
    assert shares(preview(staged)) == [34, 33, 33]
    pinned_x = {**staged, "pinned_shares": {4: 33}}
    assert shares(preview(pinned_x)) == [34, 33, 33]
    # UI removes X from active and deletes its temporary Pin, not from known IDs.
    removed = {**staged, "active_ids": [1, 2]}
    result = preview(removed)
    assert (result.mode, result.dirty, shares(result)) == ("SNAPSHOT", False, [25, 25])
    assert [(row.id, row.status, row.share) for row in result.rows] == [
        (1, "Active", 25),
        (2, "Active", 25),
        (3, "Removed", 0),
        (4, "Removed", 0),
    ]
    assert result.pinned_shares == {}
    assert shares(preview(staged)) == [34, 33, 33]  # Bring Back(X), same staged ID
    assert len(preview(base).rows) == 3  # Cancel removes X entirely


def test_staged_removed_pin_must_be_cleared() -> None:
    base = draft_payload(draft_known=[1, 2, 3, 4], pins={4: 25})
    with pytest.raises(ValidationError):
        PreviewInput.model_validate(base)


def test_empty_base_archived_pin_unpin_then_restore() -> None:
    base = draft_payload(base=[], known=[3], base_pins={3: 25}, active=[])
    before = preview(base)
    assert before.mode == "SNAPSHOT" and before.dirty is False
    assert [(row.status, row.share, row.pinned_share) for row in before.rows] == [
        ("Removed", 0, 25),
    ]
    errors({**base, "active_ids": [3], "mode": "RECALCULATED"}, "incompatible_pin")
    unpinned = {**base, "pinned_shares": {}}
    now = preview(unpinned)
    assert now.mode == "SNAPSHOT" and now.dirty and now.rows[0].pinned_share is None
    restored = preview({**unpinned, "active_ids": [3], "mode": "RECALCULATED"})
    assert shares(restored) == [100]
    assert restored.dirty and restored.mode == "RECALCULATED"
    assert preview(base).model_dump() == before.model_dump()  # Cancel


def test_empty_base_add_remove_keeps_removed_staged_entry_when_clean() -> None:
    base = draft_payload(base=[], known=[], active=[])
    staged = {**base, "draft_known_ids": [4], "active_ids": [4], "mode": "RECALCULATED"}
    assert shares(preview(staged)) == [100]
    removed = preview({**staged, "active_ids": []})
    assert (removed.mode, removed.dirty) == ("SNAPSHOT", False)
    assert [(row.id, row.status, row.share) for row in removed.rows] == [(4, "Removed", 0)]
    assert preview(base).rows == []


def test_remove_last_active_from_nonempty_base_is_rejected() -> None:
    base = draft_payload(base=[(1, 100)], known=[1], active=[])
    # To model removing the last active, start from a valid base with A active.
    base["active_ids"] = [1]
    errors({**base, "active_ids": [], "mode": "RECALCULATED"}, "empty_active_forbidden")
    errors({**base, "active_ids": [], "mode": "SNAPSHOT"}, "composition_requires_recalculated")


def test_composition_change_cannot_use_snapshot_mode() -> None:
    errors(draft_payload(active=[1, 2, 3]), "composition_requires_recalculated")


def test_all_pinned_100_is_valid_and_all_pinned_99_invalid() -> None:
    base = draft_payload()
    valid = preview({**base, "mode": "RECALCULATED", "pinned_shares": {1: 20, 2: 80}})
    assert shares(valid) == [20, 80]
    errors({**base, "mode": "RECALCULATED", "pinned_shares": {1: 20, 2: 79}}, "incompatible_pin")
    errors(
        {**base, "mode": "RECALCULATED", "pinned_shares": {1: 80, 2: 21}},
        "pinned_total_exceeds_100",
    )


def test_not_enough_percentages_for_every_active_offer() -> None:
    many = draft_payload(
        base=[],
        known=[],
        draft_known=list(range(1, 102)),
        active=list(range(1, 102)),
        mode="RECALCULATED",
    )
    errors(many, "zero_calculated_share")


@pytest.mark.parametrize("count", [1, 2, 3, 7, 33, 99, 100])
def test_remainder_distribution_is_deterministic_and_positive(count: int) -> None:
    ids = list(range(1, count + 1))
    payload = draft_payload(base=[], known=[], draft_known=ids, active=ids, mode="RECALCULATED")
    first, second = preview(payload), preview(payload)
    expected = [100 // count + (idx < 100 % count) for idx in range(count)]
    assert shares(first) == expected
    assert first.model_dump() == second.model_dump()
    assert sum(expected) == 100 and min(expected) >= 1


def test_calculation_does_not_mutate_input_or_share_mutable_global_state() -> None:
    payload = draft_payload(base_pins={3: 25})
    before = deepcopy(payload)
    result = preview(payload)
    assert payload == before
    result.rows.pop()  # Mutation of output does not alter input or future outputs.
    assert len(preview(payload).rows) == 3
    assert payload["base_pinned_shares"] == {3: 25}


@pytest.mark.parametrize(
    "field,wrong_value",
    [
        ("base_offers", [{"id": 1, "raw_share": "25"}]),
        ("base_offers", [{"id": True, "raw_share": 25}]),
        ("base_offers", [{"id": 1, "raw_share": -1}]),
        ("base_offers", [{"id": 1, "raw_share": JS_SAFE_INTEGER + 1}]),
        ("base_known_ids", [True, 2, 3]),
        ("base_known_ids", [1.0, 2, 3]),
        ("draft_known_ids", [1, 2, "3"]),
        ("active_ids", [1, 2, True]),
        ("base_pinned_shares", {3: 0}),
        ("pinned_shares", {1: 101}),
        ("pinned_shares", {1: False}),
        ("pinned_shares", {"01": 25}),
        ("pinned_shares", {"１": 25}),
        ("pinned_shares", {"9007199254740992": 25}),
        ("pinned_shares", {True: 25}),
        ("pinned_shares", {1.0: 25}),
        ("pinned_shares", {"1": 25, 1: 25}),
        ("mode", "UNKNOWN"),
        ("base_revision", 3),
        ("base_pin_revision", ""),
    ],
)
def test_invalid_types_and_bounds_are_rejected(field: str, wrong_value: Any) -> None:
    payload = draft_payload()
    payload[field] = wrong_value
    with pytest.raises(ValidationError):
        PreviewInput.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"base_offers": [{"id": 1, "raw_share": 25}, {"id": 1, "raw_share": 30}]},
        {"base_known_ids": [1, 2, 2]},
        {"draft_known_ids": [1, 2, 3, 3]},
        {"active_ids": [1, 1]},
        {"base_known_ids": [2, 3]},
        {"draft_known_ids": [1, 3, 2]},
        {"active_ids": [1, 2, 4]},
        {"base_pinned_shares": {4: 25}},
        {"pinned_shares": {4: 25}},
        {"draft_known_ids": [1, 2]},
    ],
)
def test_relationships_of_base_known_and_active_are_strict(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PreviewInput.model_validate({**draft_payload(), **change})


def test_extra_fields_and_missing_full_pin_maps_rejected() -> None:
    raw = draft_payload()
    for field in ("pinned_shares", "base_pinned_shares", "draft_known_ids"):
        invalid = {key: value for key, value in raw.items() if key != field}
        with pytest.raises(ValidationError):
            PreviewInput.model_validate(invalid)
    with pytest.raises(ValidationError):
        PreviewInput.model_validate({**raw, "credentials": "should-not-appear"})


def test_empty_base_is_only_clean_when_pins_really_match() -> None:
    base = draft_payload(base=[], known=[3], base_pins={3: 25}, active=[])
    assert not preview(base).dirty
    assert preview({**base, "pinned_shares": {}}).dirty
    errors({**base, "pinned_shares": {}, "mode": "RECALCULATED"}, "empty_active_forbidden")
