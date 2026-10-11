"""Deterministic maintenance software-twin transitions (F2).

Three maintenance actions are modelled purely as state transitions and
availability effects on the software twin — never a dispatched command:

- *schedule* — a scheduled/active ``MaintenanceState`` window makes the asset
  unavailable (``dispatch.in_maintenance``), exactly like an outage. The planner
  zeros its dispatch and the validator REJECTS any non-zero dispatch for it
  (``ASSET_UNAVAILABLE``). The deterministic validator remains the sole safety
  authority; nothing here approves or overrides it.
- *delay* — shift a flexible (``cannot_delay == False``) window to the slot that
  minimizes energy value lost over the window on the realized path.
- *dispatch crew* — shorten an outage/maintenance window; a crew never extends a
  window and never shortens it below the current slot.

Every function is pure and deterministic given the seeded realized path.
"""

from __future__ import annotations

from typing import Sequence

from .config import CREW_SPEEDUP_SLOTS, SLOT_COUNT
from .contracts import EventRecord, MaintenanceState, PortfolioFixture, ScenarioPath
from .dispatch import available_generation


def _window_length(state: MaintenanceState) -> int | None:
    if state.start_slot is None or state.end_slot is None:
        return None
    return state.end_slot - state.start_slot


def _active_windows(events: Sequence[EventRecord], asset_id: str) -> list[tuple[int, int]]:
    """Slots occupied by an *active* maintenance/outage event (hard-excluded as
    candidates for a delayed window)."""
    windows: list[tuple[int, int]] = []
    for event in events:
        if event.event_type == "bess_outage" and asset_id in event.affected_ids:
            windows.append((event.start_slot, event.end_slot))
    return windows


def _overlaps(start: int, end: int, blocked: list[tuple[int, int]]) -> bool:
    return any(start <= b_end and b_start <= end for b_start, b_end in blocked)


def delay_window(
    portfolio: PortfolioFixture,
    path: ScenarioPath,
    events: Sequence[EventRecord],
    asset_id: str,
) -> tuple[MaintenanceState, str]:
    """Shift a deferrable maintenance window to the lowest-energy-value slot.

    Returns ``(new_state, note)``. For a renewable asset the value lost is the
    summed ``available_generation`` over the window (minimize generation lost);
    for any other asset (e.g. a BESS) it is the summed ``path.price_usd_per_mwh``
    over the window on the realized path. Candidate windows are all contiguous
    equal-length windows in ``[0, SLOT_COUNT - 1]`` that do not overlap an active
    event window; the original window is always a candidate. Ties break to the
    lowest start slot. A ``cannot_delay`` window or no valid candidate returns the
    input unchanged with a "not delayable" note.
    """
    asset = next((item for item in portfolio.assets if item.asset_id == asset_id), None)
    if asset is None:
        return MaintenanceState(), "not delayable: unknown asset"
    state = getattr(asset, "maintenance", MaintenanceState())
    length = _window_length(state)
    if length is None or state.status not in ("scheduled", "active"):
        return state, "not delayable: no schedulable window"
    if state.cannot_delay:
        return state, "not delayable: window pinned (cannot_delay)"

    is_renewable = asset.kind in ("solar_farm", "wind_farm")
    blocked = _active_windows(events, asset_id)

    def value_at(start: int) -> float:
        end = start + length
        if is_renewable:
            return sum(available_generation(portfolio, path, events, slot).get(asset_id, 0.0) for slot in range(start, end + 1))
        return sum(path.price_usd_per_mwh[slot] for slot in range(start, end + 1))

    best_start = state.start_slot
    best_value = value_at(state.start_slot)
    for start in range(0, SLOT_COUNT - length):
        end = start + length
        if _overlaps(start, end, blocked) and start != state.start_slot:
            continue
        candidate_value = value_at(start)
        # Strictly-less picks a cheaper window; a tie never displaces a lower
        # start slot already chosen (iteration is ascending so first-wins == lowest).
        if candidate_value < best_value - 1e-9:
            best_value = candidate_value
            best_start = start

    if best_start == state.start_slot:
        return state, f"window {state.start_slot}..{state.end_slot} already lowest-value; not moved"
    new_state = state.model_copy(update={"start_slot": best_start, "end_slot": best_start + length})
    return new_state, f"delayed to {best_start}..{best_start + length} (lowest-value band)"


def dispatch_crew(state: MaintenanceState, slot: int, *, crew_speedup_slots: int = CREW_SPEEDUP_SLOTS) -> tuple[MaintenanceState, str]:
    """Shorten an outage/maintenance window by ``crew_speedup_slots``.

    The crew never extends the window and never shortens it below the current
    slot: ``end_slot = max(slot, end_slot - crew_speedup_slots)``. A window with
    no end slot is returned unchanged.
    """
    if state.end_slot is None:
        return state, "no end slot to shorten"
    new_end = max(slot, state.end_slot - crew_speedup_slots)
    if new_end >= state.end_slot:
        return state, "crew cannot shorten below current slot"
    new_state = state.model_copy(update={"end_slot": new_end})
    return new_state, f"crew shortened window to end at {new_end} (was {state.end_slot})"
