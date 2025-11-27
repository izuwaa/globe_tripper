import pytest

from src.state.planner_state import PlannerState, TripDetails, Demographics, Preferences, Traveler
from src.state.state_utils import is_intake_complete


def test_intake_incomplete_when_minimum_fields_missing():
    state = PlannerState()

    assert is_intake_complete(state) is False


def test_intake_complete_with_aggregate_nationality_and_counts():
    state = PlannerState(
        trip_details=TripDetails(
            destination="UK",
            origin="Nigeria",
            start_date="2024-12-01",
            end_date="2024-12-20",
        ),
        demographics=Demographics(
            adults=2,
            children=2,
            seniors=0,
            nationality=["Nigerian"],
            travelers=[
                Traveler(role="adult", nationality="Nigerian"),
                Traveler(role="adult", nationality="Nigerian"),
                Traveler(role="child", nationality="Nigerian"),
                Traveler(role="child", nationality="Nigerian"),
            ],
        ),
        preferences=Preferences(budget_mode="luxury"),
    )

    assert is_intake_complete(state) is True


def test_intake_complete_with_per_traveler_nationalities():
    state = PlannerState(
        trip_details=TripDetails(
            destination="London",
            origin="Nigeria",
            start_date="2024-12-01",
            end_date="2024-12-20",
        ),
        demographics=Demographics(
            adults=2,
            children=2,
            seniors=0,
            nationality=None,
            travelers=[
                Traveler(role="adult", nationality="Nigerian"),
                Traveler(role="adult", nationality="Nigerian"),
                Traveler(role="child", nationality="American"),
                Traveler(role="child", nationality="American"),
            ],
        ),
        preferences=Preferences(budget_mode="standard"),
    )

    assert is_intake_complete(state) is True
