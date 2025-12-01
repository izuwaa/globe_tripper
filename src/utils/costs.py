from typing import Dict, Any, List, Optional

from src.state.planner_state import PlannerState
from src.state.visa_state import VisaState, VisaRequirement
from src.state.flight_state import FlightState, FlightSearchTask, FlightSearchResult, FlightOption
from src.state.accommodation_state import (
    AccommodationState,
    AccommodationSearchTask,
    AccommodationSearchResult,
    AccommodationOption,
)


def _get_currency_bucket(
    currency_totals: Dict[str, Dict[str, float]],
    currency: Optional[str],
) -> Dict[str, float]:
    code = currency or "UNKNOWN"
    if code not in currency_totals:
        currency_totals[code] = {
            "flights_low": 0.0,
            "flights_high": 0.0,
            "accommodation_low": 0.0,
            "accommodation_high": 0.0,
        }
    return currency_totals[code]


def _aggregate_flight_costs(
    flight_state: FlightState,
    currency_totals: Dict[str, Dict[str, float]],
) -> None:
    tasks_by_id: Dict[str, FlightSearchTask] = {
        t.task_id: t for t in (flight_state.search_tasks or [])
    }

    for result in flight_state.search_results or []:
        task = tasks_by_id.get(result.task_id)
        if not task:
            continue

        chosen_type = result.chosen_option_type
        chosen_opt: Optional[FlightOption] = None

        for opt in result.options or []:
            if chosen_type and opt.option_type == chosen_type and chosen_opt is None:
                chosen_opt = opt
        if chosen_opt is None and result.options:
            chosen_opt = result.options[0]
        if chosen_opt is None:
            continue

        bucket = _get_currency_bucket(currency_totals, chosen_opt.currency)
        party_size = max(1, len(task.traveler_indexes or []))

        low = chosen_opt.total_price_low
        high = chosen_opt.total_price_high

        if low is None:
            if chosen_opt.price_per_ticket_low is not None:
                low = chosen_opt.price_per_ticket_low * party_size
            elif chosen_opt.price_per_ticket_high is not None:
                low = chosen_opt.price_per_ticket_high * party_size
        if high is None:
            if chosen_opt.price_per_ticket_high is not None:
                high = chosen_opt.price_per_ticket_high * party_size
            elif chosen_opt.price_per_ticket_low is not None:
                high = chosen_opt.price_per_ticket_low * party_size

        if low is not None:
            bucket["flights_low"] += float(low)
        if high is not None:
            bucket["flights_high"] += float(high)


def _aggregate_accommodation_costs(
    accommodation_state: AccommodationState,
    currency_totals: Dict[str, Dict[str, float]],
) -> None:
    tasks_by_id: Dict[str, AccommodationSearchTask] = {
        t.task_id: t for t in (accommodation_state.search_tasks or [])
    }

    for result in accommodation_state.search_results or []:
        task = tasks_by_id.get(result.task_id)
        if not task:
            continue

        chosen_type = result.chosen_option_type
        chosen_opt: Optional[AccommodationOption] = None

        for opt in result.options or []:
            if chosen_type and opt.option_type == chosen_type and chosen_opt is None:
                chosen_opt = opt
        if chosen_opt is None and result.options:
            chosen_opt = result.options[0]
        if chosen_opt is None:
            continue

        bucket = _get_currency_bucket(currency_totals, chosen_opt.currency)

        low = chosen_opt.total_price_low or chosen_opt.nightly_price_low
        high = chosen_opt.total_price_high or chosen_opt.nightly_price_high

        if low is not None:
            bucket["accommodation_low"] += float(low)
        if high is not None:
            bucket["accommodation_high"] += float(high)


def _collect_visa_fees(visa_state: VisaState) -> List[Dict[str, Any]]:
    fees: List[Dict[str, Any]] = []
    for idx, req in enumerate(visa_state.requirements or []):
        if not isinstance(req, VisaRequirement):
            continue
        if not req.cost:
            continue
        fees.append(
            {
                "traveler_index": idx,
                "nationality": req.nationality,
                "origin": req.origin,
                "destination": req.destination,
                "cost": req.cost,
            }
        )
    return fees


def compute_cost_summary_from_state(
    planner_state: PlannerState,
    visa_state: VisaState,
    flight_state: FlightState,
    accommodation_state: AccommodationState,
) -> Dict[str, Any]:
    """
    Pure helper that computes a rough cost summary from typed state objects.

    It does not call external services. It aggregates:
      - Flight totals per currency (low/high estimates).
      - Accommodation totals per currency (low/high estimates).
      - Simple visa fee hints (kept as text).
      - The user's budget mode and total_budget.
    """
    currency_totals: Dict[str, Dict[str, float]] = {}

    _aggregate_flight_costs(flight_state, currency_totals)
    _aggregate_accommodation_costs(accommodation_state, currency_totals)

    currency_breakdown: Dict[str, Dict[str, Any]] = {}
    for code, vals in currency_totals.items():
        flights_low = vals.get("flights_low", 0.0)
        flights_high = vals.get("flights_high", 0.0)
        accom_low = vals.get("accommodation_low", 0.0)
        accom_high = vals.get("accommodation_high", 0.0)
        currency_breakdown[code] = {
            "flights_low": flights_low or None,
            "flights_high": flights_high or None,
            "accommodation_low": accom_low or None,
            "accommodation_high": accom_high or None,
            "grand_total_low": (flights_low + accom_low) or None,
            "grand_total_high": (flights_high + accom_high) or None,
        }

    budget = {
        "mode": planner_state.preferences.budget_mode,
        "total_budget": planner_state.preferences.total_budget,
    }

    visa_fee_hints = _collect_visa_fees(visa_state)

    return {
        "currency_totals": currency_breakdown,
        "visa_fee_hints": visa_fee_hints,
        "budget": budget,
    }

