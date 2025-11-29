from google.adk.tools.tool_context import ToolContext
from src.state.planner_state import PlannerState
from src.state.visa_state import VisaState
from src.state.flight_state import FlightState


def get_planner_state(tool_context: ToolContext) -> PlannerState:
    """
    Load PlannerState from ADK's session state.

    We treat `tool_context.state` as the canonical per-session state dict
    (google.adk.sessions.state.State) and decode it into our Pydantic model.

    Args:
        tool_context (ToolContext): The context of the tool call, including session state.

    Returns:
        PlannerState: The loaded planner state.
    """
    state_obj = getattr(tool_context, "state", None)
    if state_obj is None:
        return PlannerState()

    # `State` exposes a `to_dict()` that merges base + delta.
    try:
        state_dict = state_obj.to_dict()  # type: ignore[attr-defined]
    except AttributeError:
        try:
            state_dict = dict(state_obj)
        except Exception:
            state_dict = {}

    try:
        return PlannerState.model_validate(state_dict)
    except Exception:
        return PlannerState()


def save_planner_state(tool_context: ToolContext, state: PlannerState) -> None:
    """
    Persist PlannerState into ADK's per-session state.

    ADK tracks deltas via `tool_context.state[...] = ...`, so we update
    the specific keys we own rather than replacing the whole dict.

    Args:
        tool_context (ToolContext): The context of the tool call, including session state.
        state (PlannerState): The planner state to save.

    Returns:
        None
    """
    state_obj = getattr(tool_context, "state", None)
    if state_obj is None:
        return

    state_obj["trip_details"] = state.trip_details.model_dump()
    state_obj["demographics"] = state.demographics.model_dump()
    state_obj["preferences"] = state.preferences.model_dump()
    state_obj["status"] = state.status


def get_visa_state(tool_context: ToolContext) -> VisaState:
    """
    Load VisaState from ADK's session state.

    We keep visa‑specific planning output under the "visa" key
    to avoid bloating the core PlannerState.
    """
    state_obj = getattr(tool_context, "state", None)
    if state_obj is None:
        return VisaState()

    raw = state_obj.get("visa") or {}
    try:
        return VisaState.model_validate(raw)
    except Exception:
        return VisaState()


def save_visa_state(tool_context: ToolContext, visa_state: VisaState) -> None:
    """
    Persist VisaState into ADK's per‑session state under the "visa" key.
    """
    state_obj = getattr(tool_context, "state", None)
    if state_obj is None:
        return

    state_obj["visa"] = visa_state.model_dump()


def get_flight_state(tool_context: ToolContext) -> FlightState:
    """
    Load FlightState from ADK's session state.

    Flight-specific planning output is stored under the "flights" key
    to keep it separate from the core PlannerState.
    """
    state_obj = getattr(tool_context, "state", None)
    if state_obj is None:
        return FlightState()

    raw = state_obj.get("flights") or {}
    try:
        return FlightState.model_validate(raw)
    except Exception:
        return FlightState()


def save_flight_state(tool_context: ToolContext, flight_state: FlightState) -> None:
    """
    Persist FlightState into ADK's per-session state under the "flights" key.
    """
    state_obj = getattr(tool_context, "state", None)
    if state_obj is None:
        return

    state_obj["flights"] = flight_state.model_dump()



def is_intake_complete(state: PlannerState) -> bool:
    """
    Check if the intake process is complete based on required fields.
    
    Args:
        state (PlannerState): The current planner state.
    
    Returns:
        bool: True if intake is complete, False otherwise.
        
    """
    td, demo, pref = state.trip_details, state.demographics, state.preferences

    # Origin can be provided either at the trip level or per traveler,
    # using either city names or airport codes.
    has_trip_origin = bool(td.origin) or bool(td.origin_airport_code)
    has_per_traveler_origin = bool(demo.travelers) and all(
        (t.origin or t.origin_airport_code) for t in demo.travelers
    )
    has_origin_info = has_trip_origin or has_per_traveler_origin

    # Basic trip + budget info must be present.
    basic_ok = all(
        [
            td.destination,
            td.start_date,
            td.end_date,
            pref.budget_mode is not None,
            has_origin_info,
        ]
    )

    # Headcount must be specified.
    has_counts = demo.adults is not None and demo.children is not None

    # Nationalities must be known either in aggregate or per traveler.
    has_aggregate_nationality = demo.nationality not in (None, [])

    total_expected = (demo.adults or 0) + (demo.children or 0) + (demo.seniors or 0)
    has_travelers_list = total_expected == 0 or len(demo.travelers) >= total_expected
    per_traveler_nat_ok = all(t.nationality for t in demo.travelers)

    return all(
        [
            basic_ok,
            has_counts,
            has_aggregate_nationality or per_traveler_nat_ok,
            has_travelers_list,
        ]
    )
