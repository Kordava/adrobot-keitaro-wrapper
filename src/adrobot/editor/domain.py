"""The single deterministic and side-effect-free Preview/Push calculation.

No remote mapping or archive identity is assumed here. A caller must supply
validated internal DTOs; the result is arithmetic evidence only, not a grant
to publish. All share arithmetic uses exact Python integers.
"""

from adrobot.editor.schemas import (
    DraftMode,
    PreviewInput,
    PreviewResult,
    PreviewRow,
    ShareUnit,
)


class PreviewProblem(ValueError):
    """A safe domain validation failure (HTTP Preview maps it to 422)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def calculate_preview(draft: PreviewInput) -> PreviewResult:
    """Calculate every row without changing the input, application or network.

    Priority: full base composition AND Pin equality restores SNAPSHOT/CLEAN;
    SNAPSHOT with base composition only preserves raw shares; every other
    valid change must be RECALCULATED and sum to 100 integer points.
    """
    base_ids = [offer.id for offer in draft.base_offers]
    same_composition = draft.active_ids == base_ids
    same_pins = draft.pinned_shares == draft.base_pinned_shares

    mode: DraftMode
    if same_composition and same_pins:
        mode = "SNAPSHOT"
        dirty = False
    elif draft.mode == "SNAPSHOT":
        if not same_composition:
            raise PreviewProblem(
                "composition_requires_recalculated", "Composition changes require RECALCULATED"
            )
        mode = "SNAPSHOT"
        dirty = True
    else:
        mode = "RECALCULATED"
        dirty = True

    shares: dict[int, int]
    unit: ShareUnit
    if mode == "SNAPSHOT":
        # Do not normalize, validate a sum, or constrain fixed/free/n here.
        # The base can legitimately have zero bindings, or raw 0 / >100.
        shares = {offer.id: offer.raw_share for offer in draft.base_offers}
        unit = "kt_raw"
    else:
        if not draft.active_ids:
            raise PreviewProblem(
                "empty_active_forbidden", "Removing the last active offer is not permitted"
            )
        fixed = sum(
            draft.pinned_shares[item_id]
            for item_id in draft.active_ids
            if item_id in draft.pinned_shares
        )
        if fixed > 100:
            raise PreviewProblem("pinned_total_exceeds_100", "Active pinned shares exceed 100")

        unpinned = [item_id for item_id in draft.active_ids if item_id not in draft.pinned_shares]
        free = 100 - fixed
        if not unpinned and fixed != 100:
            raise PreviewProblem("incompatible_pin", "All active pinned shares must total 100")
        if unpinned and free <= 0:
            raise PreviewProblem(
                "free_zero_with_unpinned", "Unpinned offers require positive free share"
            )
        if unpinned and free < len(unpinned):
            raise PreviewProblem(
                "zero_calculated_share", "Every active offer must receive at least one percent"
            )

        each, remainder = divmod(free, len(unpinned)) if unpinned else (0, 0)
        shares = {
            item_id: draft.pinned_shares[item_id]
            for item_id in draft.active_ids
            if item_id in draft.pinned_shares
        }
        shares.update(
            (item_id, each + (index < remainder)) for index, item_id in enumerate(unpinned)
        )
        unit = "percentage_points"

    active = set(draft.active_ids)
    rows = [
        PreviewRow(
            id=item_id,
            status="Active" if item_id in active else "Removed",
            share=shares[item_id] if item_id in active else 0,
            unit=unit if item_id in active else "percentage_points",
            pinned_share=draft.pinned_shares.get(item_id),
        )
        for item_id in draft.draft_known_ids
    ]
    return PreviewResult(
        base_revision=draft.base_revision,
        base_pin_revision=draft.base_pin_revision,
        mode=mode,
        dirty=dirty,
        active_ids=list(draft.active_ids),
        pinned_shares=dict(sorted(draft.pinned_shares.items())),
        share_unit=unit,
        rows=rows,
    )
