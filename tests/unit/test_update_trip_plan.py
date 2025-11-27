from typing import Any, Dict

from src.state.state_utils import get_planner_state
from src.tools.tools import update_trip_plan


class DummyState(dict):
    """
    Minimal stand-in for ADK's State object.

    It behaves like a plain dict and exposes a to_dict() method so that
    get_planner_state() can consume it without needing the real ADK runtime.
    """

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


class DummyToolContext:
    """
    ToolContext stub that provides the .state attribute expected by
    get_planner_state() and save_planner_state().
    """

    def __init__(self) -> None:
        self.state = DummyState()


def test_update_trip_plan_sets_basic_trip_details_and_status():
    ctx = DummyToolContext()

    result = update_trip_plan(
        tool_context=ctx,
        destination="London",
        origin="Nigeria",
        start_date="2024-12-01",
        end_date="2024-12-20",
        budget_mode="luxury",
        adults=2,
        children=2,
        nationality=["Nigerian", "American"],
    )

    assert result["status"] == "success"

    state = get_planner_state(ctx)
    assert state.trip_details.destination == "London"
    assert state.trip_details.origin == "Nigeria"
    assert state.demographics.adults == 2
    assert state.demographics.children == 2
    assert state.preferences.budget_mode == "luxury"
    # Intake should be complete with these fields populated.
    assert state.status == "planning"


def test_update_trip_plan_merges_per_traveler_details_incrementally():
    ctx = DummyToolContext()

    # First call: just set counts; travelers will be inferred.
    update_trip_plan(
        tool_context=ctx,
        adults=1,
        children=1,
        origin="Nigeria",
        nationality=["Nigerian"],
    )

    state_after_first = get_planner_state(ctx)
    assert len(state_after_first.demographics.travelers) == 2
    assert state_after_first.demographics.travelers[0].role == "adult"
    assert state_after_first.demographics.travelers[1].role == "child"

    # Second call: enrich with per-traveler details via the 'travelers' arg.
    update_trip_plan(
        tool_context=ctx,
        travelers=[
            {"role": "adult", "age": 40, "interests": ["cars"]},
            {"role": "child", "age": 8, "interests": ["planes"]},
        ],
    )

    state_after_second = get_planner_state(ctx)
    assert len(state_after_second.demographics.travelers) == 2

    adult = state_after_second.demographics.travelers[0]
    child = state_after_second.demographics.travelers[1]

    assert adult.age == 40
    assert adult.interests == ["cars"]
    assert child.age == 8
    assert child.interests == ["planes"]

