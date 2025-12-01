from typing import Optional, List, Dict, Any, Tuple, Literal
import logging
import os
import re
from datetime import date, timedelta

import requests
from google.adk.tools.tool_context import ToolContext
from src.state.planner_state import Traveler
from src.state.state_utils import (
    get_planner_state,
    save_planner_state,
    is_intake_complete,
    get_visa_state,
    save_visa_state,
    get_flight_state,
    save_flight_state,
    get_accommodation_state,
    save_accommodation_state,
    get_activity_state,
    save_activity_state,
)
from src.state.visa_state import (
    VisaState,
    VisaRequirement,
    VisaSearchTask,
    VisaSearchResult,
)
from src.state.flight_state import (
    FlightState,
    FlightSearchTask,
    FlightSearchResult,
    FlightOption,
    TravelerFlightChoice,
)
from src.state.accommodation_state import (
    AccommodationState,
    AccommodationSearchTask,
    AccommodationSearchResult,
    AccommodationOption,
    TravelerAccommodationChoice,
)
from src.state.activity_state import (
    ActivityState,
    ActivitySearchTask,
    ActivitySearchResult,
    ActivityOption,
    DayItineraryItem,
)
from src.utils.costs import compute_cost_summary_from_state


logger = logging.getLogger(__name__)


def update_trip_plan(
    tool_context: ToolContext,
    # TripDetails
    destination: Optional[str] = None,
    origin: Optional[str] = None,
    origin_airport_code: Optional[str] = None,
    destination_airport_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    flexible_dates: Optional[bool] = None,
    # Demographics (aggregate)
    adults: Optional[int] = None,
    children: Optional[int] = None,
    seniors: Optional[int] = None,
    nationality: Optional[List[str]] = None,
    # Demographics (per-traveler)
    travelers: Optional[List[Dict[str, Any]]] = None,
    # Preferences – core
    budget_mode: Optional[str] = None,
    total_budget: Optional[float] = None,
    pace: Optional[str] = None,
    interests: Optional[List[str]] = None,
    special_requests: Optional[List[str]] = None,
    notes: Optional[str] = None,
    # Preferences – accommodation & location
    accommodation_preferences: Optional[List[str]] = None,
    room_configuration: Optional[str] = None,
    neighborhood_preferences: Optional[List[str]] = None,
    neighborhood_avoid: Optional[List[str]] = None,
    # Preferences – constraints & priorities
    mobility_constraints: Optional[List[str]] = None,
    dietary_requirements: Optional[List[str]] = None,
    sensory_needs: Optional[List[str]] = None,
    must_do: Optional[List[str]] = None,
    nice_to_have: Optional[List[str]] = None,
    # Preferences – transport & rhythm
    transport_preferences: Optional[List[str]] = None,
    airport_pickup_required: Optional[bool] = None,
    luggage_count: Optional[int] = None,
    daily_rhythm: Optional[str] = None,
):
    """
    Update the planner state with any provided trip details, demographics,
    and preferences. Fields that are not provided (remain None) are left
    unchanged in the session state.

    Args:
        tool_context: ToolContext with access to the session state.
        destination: Trip destination (e.g. "UK", "London").
        origin: Origin city (e.g. "Lagos").
        start_date: Trip start date as a string (e.g. "2025-12-01").
        end_date: Trip end date as a string (e.g. "2025-12-20").
        flexible_dates: Whether dates are flexible.
        adults: Number of adult travelers.
        children: Number of child travelers.
        seniors: Number of senior travelers.
        nationality: List of traveler nationalities (e.g. ["Nigerian"]).
        budget_mode: "economy", "standard", or "luxury".
        total_budget: Total budget for the trip (numeric).
        pace: "relaxed", "moderate", or "busy".
        interests: List of interests (e.g. ["museums", "food"]).

    Returns:
        dict: Confirmation of updated planner state.
    """

    # Get current typed state
    state = get_planner_state(tool_context)

    logger.info(
        "[Tool] update_trip_plan called",
        extra={
            "app_name": getattr(getattr(tool_context, "_invocation_context", None), "app_name", None),
            "user_id": getattr(tool_context, "user_id", None),
        },
    )
    # Lightweight debug to see if the model is providing per-traveler details.
    print(f"[Tool DEBUG] travelers arg: {travelers}")

    # ---- TripDetails ----
    if destination is not None:
        state.trip_details.destination = destination
    if origin is not None:
        state.trip_details.origin = origin
    if origin_airport_code is not None:
        state.trip_details.origin_airport_code = origin_airport_code
    if destination_airport_code is not None:
        state.trip_details.destination_airport_code = destination_airport_code
    if start_date is not None:
        state.trip_details.start_date = start_date
    if end_date is not None:
        state.trip_details.end_date = end_date
    if flexible_dates is not None:
        state.trip_details.flexible_dates = flexible_dates
    # ---- Demographics (aggregate) ----
    if adults is not None:
        state.demographics.adults = adults
    if children is not None:
        state.demographics.children = children
    if seniors is not None:
        state.demographics.seniors = seniors
    if nationality is not None:
        state.demographics.nationality = nationality

    # ---- Demographics (per-traveler) ----
    # Start from existing travelers and merge incremental updates instead of replacing.
    existing_travelers: List[Traveler] = list(state.demographics.travelers or [])
    normalized_travelers: List[Traveler] = existing_travelers[:]

    if travelers is not None:
        for idx, t in enumerate(travelers):
            if not isinstance(t, dict):
                continue
            try:
                incoming = Traveler.model_validate(t)
            except Exception:
                continue

            base = normalized_travelers[idx] if idx < len(normalized_travelers) else Traveler(role=incoming.role)

            merged = Traveler(
                role=incoming.role or base.role,
                age=incoming.age if incoming.age is not None else base.age,
                nationality=incoming.nationality or base.nationality,
                origin=incoming.origin or base.origin,
                origin_airport_code=incoming.origin_airport_code or base.origin_airport_code,
                luggage_count=incoming.luggage_count if incoming.luggage_count is not None else base.luggage_count,
                interests=incoming.interests or base.interests,
                mobility_needs=incoming.mobility_needs or base.mobility_needs,
                dietary_needs=incoming.dietary_needs or base.dietary_needs,
                sensory_needs=incoming.sensory_needs or base.sensory_needs,
                special_requirements=incoming.special_requirements or base.special_requirements,
            )

            if idx < len(normalized_travelers):
                normalized_travelers[idx] = merged
            else:
                normalized_travelers.append(merged)

    # If we still have no travelers, infer a basic list from aggregate counts
    # so that downstream logic and intake completion have per-person placeholders.
    if not normalized_travelers:
        total_expected = (state.demographics.adults or 0) + (state.demographics.children or 0) + (state.demographics.seniors or 0)
        if total_expected:
            inferred: List[Traveler] = []
            default_origin = state.trip_details.origin
            default_nat = (state.demographics.nationality or [None])[0]

            for _ in range(state.demographics.adults or 0):
                inferred.append(Traveler(role="adult", age=None, nationality=default_nat, origin=default_origin))
            for _ in range(state.demographics.children or 0):
                inferred.append(Traveler(role="child", age=None, nationality=default_nat, origin=default_origin))
            for _ in range(state.demographics.seniors or 0):
                inferred.append(Traveler(role="senior", age=None, nationality=default_nat, origin=default_origin))

            normalized_travelers = inferred

    if normalized_travelers:
        state.demographics.travelers = normalized_travelers
        # If aggregate nationality is still unset but per-traveler nationalities
        # are known, infer a compact list of unique nationalities so downstream
        # logic and agents have both views available without re-asking.
        if state.demographics.nationality in (None, []):
            nat_values = {
                t.nationality
                for t in normalized_travelers
                if t.nationality
            }
            if nat_values:
                state.demographics.nationality = sorted(nat_values)
    # ---- Preferences ----
    if budget_mode is not None:
        state.preferences.budget_mode = budget_mode
        # Optional: clear total_budget when "luxury"
        if budget_mode == "luxury":
            state.preferences.total_budget = None
    if total_budget is not None:
        state.preferences.total_budget = total_budget
    if pace is not None:
        state.preferences.pace = pace
    if interests is not None:
        # Replace the full set of interests when explicitly provided.
        state.preferences.interests = interests
    if special_requests is not None:
        existing_requests = state.preferences.special_requests or []
        for req in special_requests:
            if req and req not in existing_requests:
                existing_requests.append(req)
        state.preferences.special_requests = existing_requests
    if notes is not None:
        existing_notes = (state.preferences.notes or "").strip()
        if existing_notes and notes not in existing_notes:
            state.preferences.notes = f"{existing_notes} {notes}".strip()
        else:
            state.preferences.notes = notes

    # Accommodation & location
    if accommodation_preferences is not None:
        state.preferences.accommodation_preferences = accommodation_preferences
    if room_configuration is not None:
        state.preferences.room_configuration = room_configuration
    if neighborhood_preferences is not None:
        state.preferences.neighborhood_preferences = neighborhood_preferences
    if neighborhood_avoid is not None:
        state.preferences.neighborhood_avoid = neighborhood_avoid

    # Constraints & priorities
    if mobility_constraints is not None:
        state.preferences.mobility_constraints = mobility_constraints
    if dietary_requirements is not None:
        state.preferences.dietary_requirements = dietary_requirements
    if sensory_needs is not None:
        state.preferences.sensory_needs = sensory_needs
    if must_do is not None:
        state.preferences.must_do = must_do
    if nice_to_have is not None:
        state.preferences.nice_to_have = nice_to_have

    # Transport & rhythm
    if transport_preferences is not None:
        state.preferences.transport_preferences = transport_preferences
    if airport_pickup_required is not None:
        state.preferences.airport_pickup_required = airport_pickup_required
    if luggage_count is not None:
        state.preferences.luggage_count = luggage_count
    if daily_rhythm is not None:
        state.preferences.daily_rhythm = daily_rhythm

    # Write back into state and session
    save_planner_state(tool_context, state)

    dump_state = state.model_dump()
    logger.info(
        "[Tool] update_trip_plan completed",
        extra={
            "status": state.status,
            "trip_destination": state.trip_details.destination,
            "trip_origin": state.trip_details.origin,
            "num_adults": state.demographics.adults,
            "num_children": state.demographics.children,
            "budget_mode": state.preferences.budget_mode,
        },
    )
    print(f"[Tool] Current State after update: {dump_state}")

    return {"status": "success", "updated_state": dump_state}


def assess_visa_requirements(tool_context: ToolContext) -> dict[str, Any]:
    """
    Derive high‑level visa requirements for each traveler based on the
    current PlannerState and persist them into VisaState.

    This function does not itself call external services; it is designed
    to be paired with an LLM + google_search tool that can fill in the
    descriptive fields of VisaRequirement based on up‑to‑date rules.

    Args:
        tool_context (ToolContext): The context of the tool call, including session state.

    Returns:
        dict: Confirmation of updated visa state.
    """
    planner_state = get_planner_state(tool_context)
    visa_state = get_visa_state(tool_context)

    requirements: List[VisaRequirement] = []

    # For flight searches, prefer a concrete destination airport code when
    # available (e.g. "LHR"); fall back to the broader destination string
    # otherwise (e.g. "London", "UK").
    # For flight searches we require a concrete arrival airport code so that
    # downstream tools can call external APIs reliably. If it is missing, we
    # skip task derivation and let higher-level agents ask the user to specify it.
    destination_airport = planner_state.trip_details.destination_airport_code
    if not destination_airport:
        logger.info(
            "[Tool] derive_flight_search_tasks skipped – missing destination_airport_code",
        )
        return {"status": "skipped", "reason": "missing_destination_airport_code"}

    destination = destination_airport
    travelers = planner_state.demographics.travelers or []

    for idx, traveler in enumerate(travelers):
        req = VisaRequirement(
            traveler_index=idx,
            origin=traveler.origin or planner_state.trip_details.origin,
            destination=destination,
            nationality=traveler.nationality,
        )
        requirements.append(req)

    # Replace existing requirements with the newly derived skeleton.
    visa_state.requirements = requirements
    # Leave overall_summary for an LLM‑driven agent to populate.

    save_visa_state(tool_context, visa_state)

    dump_state = visa_state.model_dump()
    logger.info(
        "[Tool] assess_visa_requirements completed",
        extra={
            "num_travelers": len(requirements),
            "destination": destination,
        },
    )
    print(f"[Tool] Current VisaState after update: {dump_state}")

    return {"status": "success", "visa_state": dump_state}


def derive_visa_search_tasks(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Build VisaSearchTask objects based on the current PlannerState and VisaState.

    Travelers who share the same nationality and destination are grouped
    into a single task so that the search agent can reuse work.
    """
    planner_state = get_planner_state(tool_context)
    visa_state = get_visa_state(tool_context)

    destination = planner_state.trip_details.destination
    travelers = planner_state.demographics.travelers or []

    if not destination or not travelers:
        logger.info(
            "[Tool] derive_visa_search_tasks skipped – missing destination or travelers",
        )
        return {"status": "skipped", "reason": "missing_destination_or_travelers"}

    # Group travelers by (nationality, destination_country).
    groups: Dict[Tuple[Optional[str], str], List[int]] = {}
    for idx, traveler in enumerate(travelers):
        key = (traveler.nationality, destination)
        groups.setdefault(key, []).append(idx)

    tasks: List[VisaSearchTask] = []
    for (nationality, dest_country), indexes in groups.items():
        task_id = f"{nationality or 'unknown'}_{dest_country}_{len(visa_state.search_tasks) + len(tasks)}"
        task = VisaSearchTask(
            task_id=task_id,
            traveler_indexes=indexes,
            origin_country=planner_state.trip_details.origin,
            destination_country=dest_country,
            nationality=nationality,
            travel_purpose="tourism",
        )
        tasks.append(task)

    # Append to existing tasks; callers may rerun this, so avoid wiping previous results.
    visa_state.search_tasks.extend(tasks)
    save_visa_state(tool_context, visa_state)

    logger.info(
        "[Tool] derive_visa_search_tasks completed",
        extra={
            "num_groups": len(groups),
            "num_tasks_created": len(tasks),
        },
    )

    print(f"[Tool] derive_visa_search_tasks created {len(tasks)} task(s)")

    return {
        "status": "success",
        "num_tasks_created": len(tasks),
        "tasks": [t.model_dump() for t in tasks],
    }


def derive_flight_search_tasks(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Build FlightSearchTask objects based on the current PlannerState, VisaState, and FlightState.

    Travelers who share the same origin city and destination are grouped into a single task so that
    search work can be reused. Dates are made visa-aware by shifting the recommended departure date
    when visa processing hints suggest a later earliest safe departure.
    """
    planner_state = get_planner_state(tool_context)
    visa_state = get_visa_state(tool_context)
    flight_state = get_flight_state(tool_context)

    # For flight searches we *require* a concrete destination airport code so
    # downstream tools can call external APIs reliably. If it is missing, we
    # skip task derivation and let higher-level agents resolve it first.
    destination_airport = planner_state.trip_details.destination_airport_code
    if not destination_airport:
        logger.info(
            "[Tool] derive_flight_search_tasks skipped – missing destination_airport_code",
        )
        return {"status": "skipped", "reason": "missing_destination_airport_code"}

    destination = destination_airport
    origin_default = (
        planner_state.trip_details.origin_airport_code
        or planner_state.trip_details.origin
    )
    start_date = planner_state.trip_details.start_date
    end_date = planner_state.trip_details.end_date
    flexible_dates = planner_state.trip_details.flexible_dates
    budget_mode = planner_state.preferences.budget_mode

    travelers = planner_state.demographics.travelers or []

    if not destination or not travelers or not start_date:
        logger.info(
            "[Tool] derive_flight_search_tasks skipped – missing destination, travelers, or start_date",
        )
        return {"status": "skipped", "reason": "missing_destination_travelers_or_start_date"}

    # Visa-aware date shifting: compute recommended departure based on earliest_safe_departure_date.
    original_departure_date = start_date
    original_return_date = end_date

    recommended_departure_date = original_departure_date
    recommended_return_date = original_return_date
    visa_reason: Optional[str] = None

    try:
        dep_dt = date.fromisoformat(original_departure_date)
    except Exception:
        dep_dt = None

    safe_dep_dt: Optional[date] = None
    if visa_state.earliest_safe_departure_date:
        try:
            safe_dep_dt = date.fromisoformat(visa_state.earliest_safe_departure_date)
        except Exception:
            safe_dep_dt = None

    if dep_dt and safe_dep_dt and safe_dep_dt > dep_dt:
        recommended_departure_date = safe_dep_dt.isoformat()
        visa_reason = (
            "Departure date adjusted to respect visa processing timelines; "
            f"earliest safe departure estimated as {recommended_departure_date}."
        )
        if original_return_date:
            try:
                ret_dt = date.fromisoformat(original_return_date)
            except Exception:
                ret_dt = None

            if ret_dt:
                # If the visa-safe departure would push the trip to start on or
                # after the originally requested return date, extend the return
                # to preserve at least the original trip length (or a minimum
                # of 3 days if the original length was invalid/zero).
                if safe_dep_dt >= ret_dt:
                    delta = ret_dt - dep_dt if dep_dt else None
                    if not delta or delta.days <= 0:
                        delta = timedelta(days=3)
                    recommended_return_date = (safe_dep_dt + delta).isoformat()
                elif flexible_dates:
                    # Standard case: preserve trip length when dates are flexible.
                    try:
                        delta = ret_dt - dep_dt
                        recommended_return_date = (safe_dep_dt + delta).isoformat()
                    except Exception:
                        recommended_return_date = original_return_date
                else:
                    # Dates not flexible and safe departure is still before the
                    # original return; keep the user's requested end date.
                    recommended_return_date = original_return_date

    # Group travelers by (origin_city, destination).
    groups: Dict[Tuple[Optional[str], str], List[int]] = {}
    for idx, traveler in enumerate(travelers):
        origin_city = traveler.origin_airport_code or traveler.origin or origin_default
        key = (origin_city, destination)
        groups.setdefault(key, []).append(idx)

    tasks: List[FlightSearchTask] = []
    for (origin_city, dest_city), indexes in groups.items():
        task_id = f"flight_{origin_city or 'unknown'}_{dest_city}_{len(flight_state.search_tasks) + len(tasks)}"

        # Cabin preference heuristic from budget_mode.
        cabin_pref: Optional[str] = None
        if budget_mode == "luxury":
            cabin_pref = "business"
        else:
            cabin_pref = "economy"

        prompt = (
            "Search for typical round-trip flight options for the following context:\n"
            f"- Origin: {origin_city or 'UNKNOWN ORIGIN'}\n"
            f"- Destination: {dest_city or 'UNKNOWN DESTINATION'}\n"
            f"- Original departure date: {original_departure_date or 'UNKNOWN'}\n"
            f"- Original return date: {original_return_date or 'UNKNOWN'}\n"
            f"- Recommended departure date (visa-aware): {recommended_departure_date or 'UNKNOWN'}\n"
            f"- Recommended return date: {recommended_return_date or 'UNKNOWN'}\n"
            f"- Cabin preference: {cabin_pref or 'unspecified'}\n"
            f"- Budget mode: {budget_mode or 'unspecified'}\n"
            f"- Travelers covered (indexes): {indexes}\n"
            "- Grouping intent: travelers in this task form one traveling party from the same origin. "
            "Prefer itineraries that keep them on the same flights where practical. If that is not possible "
            "(for example due to availability or price constraints), choose well-coordinated alternatives "
            "with similar arrival/departure times and briefly note this in your summary.\n\n"
            "Identify:\n"
            "- The cheapest reasonable option (avoid extremely long or multi-day itineraries).\n"
            "- The fastest reasonable option.\n"
            "- A balanced option that trades off time and cost appropriately for the budget mode.\n"
            "For each, provide duration, number of stops, typical carriers, and approximate price range."
        )

        task = FlightSearchTask(
            task_id=task_id,
            traveler_indexes=indexes,
            origin_city=origin_city,
            destination_city=dest_city,
            original_departure_date=original_departure_date,
            original_return_date=original_return_date,
            recommended_departure_date=recommended_departure_date,
            recommended_return_date=recommended_return_date,
            visa_timeline_reason=visa_reason,
            cabin_preference=cabin_pref,
            budget_mode=budget_mode,
            flexibility_hint=None,
            prompt=prompt,
            purpose="flight_options_lookup",
        )
        tasks.append(task)

    flight_state.search_tasks.extend(tasks)
    save_flight_state(tool_context, flight_state)

    logger.info(
        "[Tool] derive_flight_search_tasks completed",
        extra={
            "num_groups": len(groups),
            "num_tasks_created": len(tasks),
            "recommended_departure_date": recommended_departure_date,
        },
    )

    print(f"[Tool] derive_flight_search_tasks created {len(tasks)} flight task(s)")

    return {
        "status": "success",
        "num_tasks_created": len(tasks),
        "tasks": [t.model_dump() for t in tasks],
    }


def read_visa_search_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lightweight reader for the current VisaState search-related fields.

    Intended for use by search-focused agents so they can see which
    VisaSearchTasks already exist and which VisaSearchResults have been
    populated, without needing to know how state is stored.
    """
    visa_state = get_visa_state(tool_context)
    dump_state = visa_state.model_dump()

    logger.info(
        "[Tool] read_visa_search_state called",
        extra={
            "num_search_tasks": len(visa_state.search_tasks),
            "num_search_results": len(visa_state.search_results),
        },
    )

    return {
        "search_tasks": dump_state.get("search_tasks", []),
        "search_results": dump_state.get("search_results", []),
    }


def read_accommodation_search_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lightweight reader for the current AccommodationState search-related fields.

    Intended for use by search-focused agents so they can see which
    AccommodationSearchTasks already exist and which AccommodationSearchResults
    have been populated, without needing to know how state is stored.
    """
    accommodation_state = get_accommodation_state(tool_context)
    dump_state = accommodation_state.model_dump()

    logger.info(
        "[Tool] read_accommodation_search_state called",
        extra={
            "num_search_tasks": len(accommodation_state.search_tasks),
            "num_search_results": len(accommodation_state.search_results),
        },
    )

    return {
        "search_tasks": dump_state.get("search_tasks", []),
        "search_results": dump_state.get("search_results", []),
    }


def compute_cost_summary(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Tool wrapper around compute_cost_summary_from_state.

    This allows agents to quickly retrieve an aggregated view of:
      - Flight and accommodation costs per currency.
      - Simple visa fee hints (as text).
      - The user's budget_mode and total_budget.
    """
    planner_state = get_planner_state(tool_context)
    visa_state = get_visa_state(tool_context)
    flight_state = get_flight_state(tool_context)
    accommodation_state = get_accommodation_state(tool_context)

    summary = compute_cost_summary_from_state(
        planner_state=planner_state,
        visa_state=visa_state,
        flight_state=flight_state,
        accommodation_state=accommodation_state,
    )

    logger.info(
        "[Tool] compute_cost_summary completed",
        extra={
            "num_currencies": len(summary.get("currency_totals") or {}),
            "has_budget": summary.get("budget", {}).get("total_budget") is not None,
        },
    )

    return {"status": "success", **summary}


def record_visa_search_result(
    tool_context: ToolContext,
    task_id: str,
    summary: str,
    processing_time_hint: Optional[str] = None,
    fee_hint: Optional[str] = None,
    notes: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Persist a normalized VisaSearchResult into VisaState for a given task_id.

    This tool does NOT call external services. It relies on the agent
    to pass in a concise summary and any extracted hints based on prior
    google_search calls.
    """
    visa_state = get_visa_state(tool_context)

    matching_task = next((t for t in visa_state.search_tasks if t.task_id == task_id), None)
    if matching_task is None:
        logger.warning(
            "[Tool] record_visa_search_result called with unknown task_id",
            extra={"task_id": task_id},
        )
        return {"status": "error", "reason": "unknown_task_id", "task_id": task_id}

    query = matching_task.prompt
    jurisdiction = matching_task.destination_country

    result = VisaSearchResult(
        task_id=task_id,
        query=query,
        jurisdiction=jurisdiction,
        summary=summary,
        sources=sources,
        processing_time_hint=processing_time_hint,
        fee_hint=fee_hint,
        notes=notes,
    )

    visa_state.search_results.append(result)
    save_visa_state(tool_context, visa_state)

    logger.info(
        "[Tool] record_visa_search_result completed",
        extra={
            "task_id": task_id,
            "jurisdiction": jurisdiction,
            "num_results_total": len(visa_state.search_results),
        },
    )

    print(
        f"[Visa Result Tool] Recorded VisaSearchResult for task_id={task_id}, "
        f"jurisdiction={jurisdiction}"
    )

    return {
        "status": "success",
        "task_id": task_id,
        "num_results_total": len(visa_state.search_results),
    }


def apply_visa_search_results(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Apply the normalized VisaSearchResult entries back onto VisaRequirement
    objects in VisaState.

    This tool does not call external services. It performs a best-effort
    mapping from search_results to per-traveler VisaRequirement fields:

    - For each VisaSearchResult, locate the corresponding VisaSearchTask.
    - For each traveler_index in that task, ensure a VisaRequirement exists
      (creating a minimal one if necessary).
    - Copy the processing_time_hint and fee_hint onto the requirement.
    - Attach the search summary and notes into additional_notes, preserving
      any existing notes where possible.

    It does NOT attempt deep NLP extraction; instead, it records the
    high-level findings so downstream agents or UIs can present them.
    """
    visa_state = get_visa_state(tool_context)

    # Fast path: nothing to apply.
    if not visa_state.search_results:
        logger.info("[Tool] apply_visa_search_results skipped – no search_results present")
        return {"status": "skipped", "reason": "no_search_results"}

    # Build quick lookup maps.
    tasks_by_id: Dict[str, VisaSearchTask] = {
        t.task_id: t for t in (visa_state.search_tasks or [])
    }
    requirements_by_traveler: Dict[int, VisaRequirement] = {
        r.traveler_index: r for r in (visa_state.requirements or [])
    }

    updated_travelers: List[int] = []
    processing_day_hints: List[int] = []

    for result in visa_state.search_results:
        task = tasks_by_id.get(result.task_id)
        if not task:
            logger.warning(
                "[Tool] apply_visa_search_results found result without matching task",
                extra={"task_id": result.task_id},
            )
            continue

        for traveler_index in task.traveler_indexes or []:
            req = requirements_by_traveler.get(traveler_index)
            if not req:
                # Create a minimal requirement record if none exists yet.
                req = VisaRequirement(
                    traveler_index=traveler_index,
                    origin=task.origin_country,
                    destination=task.destination_country,
                    nationality=task.nationality,
                )
                visa_state.requirements.append(req)
                requirements_by_traveler[traveler_index] = req

            # Build a combined text corpus to derive simple booleans/types.
            text_corpus_parts: List[str] = []
            if result.summary:
                text_corpus_parts.append(result.summary.lower())
            if result.notes:
                text_corpus_parts.append(result.notes.lower())
            corpus = " ".join(text_corpus_parts)

            # --- needs_visa heuristic ---
            needs_visa: Optional[bool] = req.needs_visa
            if "no visa required" in corpus or "do not require a visa" in corpus:
                needs_visa = False
            elif "visa required" in corpus or "require a visa" in corpus:
                # Avoid overriding explicit "no visa required" cases above.
                if needs_visa is None:
                    needs_visa = True
            req.needs_visa = needs_visa

            # --- visa_type heuristic ---
            visa_type = req.visa_type or None
            if "standard visitor visa" in corpus:
                visa_type = "Standard Visitor Visa"
            elif "tourist visa" in corpus:
                visa_type = "Tourist Visa"
            elif "electronic travel authorization" in corpus or " eta " in corpus:
                visa_type = "Electronic Travel Authorization (ETA)"
            req.visa_type = visa_type

            # Build a combined text corpus to derive simple booleans/types.
            text_corpus_parts: List[str] = []
            if result.summary:
                text_corpus_parts.append(result.summary.lower())
            if result.notes:
                text_corpus_parts.append(result.notes.lower())
            corpus = " ".join(text_corpus_parts)

            # --- needs_visa heuristic ---
            needs_visa: Optional[bool] = req.needs_visa
            if "no visa required" in corpus or "do not require a visa" in corpus:
                needs_visa = False
            elif "visa required" in corpus or "require a visa" in corpus:
                if needs_visa is None:
                    needs_visa = True
            req.needs_visa = needs_visa

            # --- visa_type heuristic ---
            visa_type = req.visa_type or None
            if "standard visitor visa" in corpus:
                visa_type = "Standard Visitor Visa"
            elif "tourist visa" in corpus:
                visa_type = "Tourist Visa"
            elif "electronic travel authorization" in corpus or " eta " in corpus:
                visa_type = "Electronic Travel Authorization (ETA)"
            req.visa_type = visa_type

            # Update simple scalar hints.
            if result.processing_time_hint:
                req.processing_time = result.processing_time_hint
                # Extract numeric day hints for earliest_safe_departure_date.
                numbers = [int(n) for n in re.findall(r"\d+", result.processing_time_hint)]
                if numbers:
                    processing_day_hints.append(max(numbers))
            if result.fee_hint:
                req.cost = result.fee_hint

            # Attach summary + notes as additional_notes for downstream consumption.
            notes_chunks: List[str] = []
            if result.summary:
                notes_chunks.append(result.summary.strip())
            if result.notes:
                notes_chunks.append(result.notes.strip())

            combined = "\n\n".join([c for c in notes_chunks if c])
            if combined:
                existing = (req.additional_notes or "").strip()
                if existing and combined not in existing:
                    req.additional_notes = f"{existing}\n\n{combined}"
                elif not existing:
                    req.additional_notes = combined

            updated_travelers.append(traveler_index)

    # Compute a conservative earliest_safe_departure_date based on processing hints.
    if processing_day_hints:
        max_days = max(processing_day_hints)
        max_days = max(1, min(max_days, 120))  # clamp to a sensible range
        earliest = date.today() + timedelta(days=max_days)
        visa_state.earliest_safe_departure_date = earliest.isoformat()

    save_visa_state(tool_context, visa_state)

    logger.info(
        "[Tool] apply_visa_search_results completed",
        extra={
            "num_requirements": len(visa_state.requirements),
            "num_results": len(visa_state.search_results),
            "num_travelers_updated": len(set(updated_travelers)),
        },
    )

    print(
        f"[Tool] apply_visa_search_results updated requirements for "
        f"{len(set(updated_travelers))} traveler(s)"
    )

    return {
        "status": "success",
        "num_requirements": len(visa_state.requirements),
        "num_results": len(visa_state.search_results),
        "num_travelers_updated": len(set(updated_travelers)),
    }


def record_flight_search_result(
    tool_context: ToolContext,
    task_id: str,
    summary: str,
    options: Optional[List[Dict[str, Any]]] = None,
    best_price_hint: Optional[str] = None,
    best_time_hint: Optional[str] = None,
    cheap_but_long_hint: Optional[str] = None,
    recommended_option_label: Optional[str] = None,
    notes: Optional[str] = None,
    chosen_option_type: Optional[str] = None,
    selection_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist a normalized FlightSearchResult into FlightState for a given task_id.

    This tool does NOT call external services. It relies on the agent
    to pass in a concise summary and extracted hints based on prior
    flight search calls.
    """
    flight_state = get_flight_state(tool_context)

    matching_task = next((t for t in flight_state.search_tasks if t.task_id == task_id), None)
    if matching_task is None:
        logger.warning(
            "[Tool] record_flight_search_result called with unknown task_id",
            extra={"task_id": task_id},
        )
        return {"status": "error", "reason": "unknown_task_id", "task_id": task_id}

    query = matching_task.prompt

    option_models: List[FlightOption] = []
    for opt in options or []:
        try:
            option_models.append(FlightOption(**opt))
        except Exception as exc:
            logger.warning(
                "[Tool] record_flight_search_result could not parse option",
                extra={"task_id": task_id, "option": opt, "error": str(exc)},
            )

    result = FlightSearchResult(
        task_id=task_id,
        query=query,
        options=option_models,
        summary=summary,
        best_price_hint=best_price_hint,
        best_time_hint=best_time_hint,
        cheap_but_long_hint=cheap_but_long_hint,
        recommended_option_label=recommended_option_label,
        notes=notes,
        chosen_option_type=chosen_option_type,
        selection_reason=selection_reason,
    )

    flight_state.search_results.append(result)
    save_flight_state(tool_context, flight_state)

    logger.info(
        "[Tool] record_flight_search_result completed",
        extra={
            "task_id": task_id,
            "num_results_total": len(flight_state.search_results),
        },
    )

    print(f"[Flight Result Tool] Recorded FlightSearchResult for task_id={task_id}")

    return {
        "status": "success",
        "task_id": task_id,
        "num_results_total": len(flight_state.search_results),
    }


def derive_accommodation_search_tasks(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Build AccommodationSearchTask objects based on the current PlannerState
    (and optionally refined by FlightState).

    For now, we create a single task that covers all travelers for the main
    destination, using:
    - location from PlannerState.trip_details.destination
    - check_in/check_out from PlannerState.trip_details start/end dates
    - budget_mode and accommodation/location preferences from PlannerState.preferences

    Later we can extend this to support multi-city segments and use
    FlightState.traveler_flights to tighten the stay window.
    """
    planner_state = get_planner_state(tool_context)
    accommodation_state = get_accommodation_state(tool_context)

    destination = planner_state.trip_details.destination
    planner_start_date = planner_state.trip_details.start_date
    planner_end_date = planner_state.trip_details.end_date
    travelers = planner_state.demographics.travelers or []

    if not travelers:
        logger.info(
            "[Tool] derive_accommodation_search_tasks skipped – missing destination or travelers",
        )
        return {"status": "skipped", "reason": "missing_destination_or_travelers"}

    traveler_indexes = list(range(len(travelers)))

    # Default to planner start/end dates, but tighten the window if we have
    # concrete flight choices with arrival/departure datetimes.
    check_in_date = planner_start_date
    check_out_date = planner_end_date

    # Prefer a city-level location for accommodation searches. If the planner
    # destination is very broad (e.g. just "UK") but we have a concrete
    # destination airport code from flight planning, infer a likely base city
    # from that airport (for example, LHR -> "London, UK").
    accommodation_location = destination

    flight_state = get_flight_state(tool_context)
    arrival_dates: List[str] = []
    departure_dates: List[str] = []

    for choice in flight_state.traveler_flights or []:
        if choice.traveler_index not in traveler_indexes:
            continue

        option = choice.chosen_option
        # If no chosen_option recorded (e.g. stub result), fall back to first other_option.
        if option is None and choice.other_options:
            option = choice.other_options[0]
        if option is None:
            continue

        if option.outbound_arrival and len(option.outbound_arrival) >= 10:
            arrival_dates.append(option.outbound_arrival[:10])

        # For departure, prefer return_departure; if missing, fall back to return_arrival.
        departure_str = option.return_departure or option.return_arrival
        if departure_str and len(departure_str) >= 10:
            departure_dates.append(departure_str[:10])

    if arrival_dates:
        check_in_date = min(arrival_dates)
    if departure_dates:
        check_out_date = max(departure_dates)

    # Guard against inverted date windows (e.g. arrival after planner end date).
    if (
        check_in_date
        and check_out_date
        and check_in_date > check_out_date
    ):
        if planner_start_date and planner_end_date and planner_start_date <= planner_end_date:
            check_in_date = planner_start_date
            check_out_date = planner_end_date
        else:
            check_out_date = check_in_date

    # Infer base city from flight destination when destination is missing or very broad.
    # This keeps accommodation queries anchored to a realistic city (e.g. "London, UK").
    if not accommodation_location or accommodation_location.strip().upper() in {"UK", "UNITED KINGDOM"}:
        # Look at any flight search task to see the destination city/airport code.
        if flight_state.search_tasks:
            dest_code = flight_state.search_tasks[0].destination_city
            if dest_code:
                code = dest_code.strip().upper()
                # Simple mappings for common UK airports; extend as needed.
                london_airports = {"LHR", "LGW", "LCY", "STN", "LTN", "SEN"}
                if code in london_airports:
                    accommodation_location = "London, UK"

    # Fall back to the original destination string if no better inference is available.
    if not accommodation_location:
        accommodation_location = destination

    pref = planner_state.preferences
    budget_mode = pref.budget_mode
    preferred_types = pref.accommodation_preferences or []
    neighborhood_prefs = pref.neighborhood_preferences or []
    neighborhood_avoid = pref.neighborhood_avoid or []
    room_configuration = pref.room_configuration

    # Collect simple age/role context so downstream agents can respect
    # common-sense constraints (e.g. young children not staying alone).
    child_ages: List[int] = []
    adult_count = 0
    for t in travelers:
        if t.role == "adult":
            adult_count += 1
        elif t.role == "child" and t.age is not None:
            child_ages.append(t.age)

    special_reqs: List[str] = []
    for group in [
        pref.mobility_constraints or [],
        pref.dietary_requirements or [],
        pref.sensory_needs or [],
    ]:
        for item in group:
            if item and item not in special_reqs:
                special_reqs.append(item)

    task_id = f"accommodation_{len(accommodation_state.search_tasks)}"

    prompt_lines = [
        "Search for suitable accommodation options for the following trip context:",
        f"- Destination: {accommodation_location}",
        f"- Check-in date: {check_in_date or 'UNKNOWN'}",
        f"- Check-out date: {check_out_date or 'UNKNOWN'}",
        f"- Budget mode: {budget_mode or 'unspecified'}",
        f"- Travelers covered (indexes): {traveler_indexes}",
        f"- Number of adults: {adult_count}",
        f"- Child ages (if any): {child_ages or 'none'}",
    ]
    if preferred_types:
        prompt_lines.append(f"- Preferred stay types: {preferred_types}")
    if room_configuration:
        prompt_lines.append(f"- Room configuration: {room_configuration}")
    if neighborhood_prefs:
        prompt_lines.append(f"- Neighborhoods to prioritize: {neighborhood_prefs}")
    if neighborhood_avoid:
        prompt_lines.append(f"- Neighborhoods to avoid: {neighborhood_avoid}")
    if special_reqs:
        prompt_lines.append(f"- Special requirements: {special_reqs}")
    # Grouping intent: this is one traveling party who would generally prefer
    # to stay in the same property (hotel or rental) if practical. If the
    # property/room layout requires multiple rooms or units, keep the party
    # together in the same property and clearly explain the grouping in your
    # summary instead of splitting them across unrelated properties.
    prompt_lines.append(
        "- Grouping intent: the travelers listed above form one traveling party. "
        "Prefer a single property that can host the whole group. If that is not "
        "practical, keep them in the same property (for example multiple rooms in "
        "one hotel or multiple units in one building) and briefly explain the "
        "grouping in your summary."
    )
    # Safety hint so downstream agents avoid assigning very young
    # children to rooms without an adult.
    if child_ages and adult_count > 0:
        youngest = min(child_ages)
        prompt_lines.append(
            f"- Important: the youngest child is {youngest} years old; do NOT assign children to rooms "
            "without at least one adult present."
        )

    prompt = "\n".join(prompt_lines)

    task = AccommodationSearchTask(
        task_id=task_id,
        traveler_indexes=traveler_indexes,
        location=accommodation_location,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        budget_mode=budget_mode,
        preferred_types=preferred_types,
        neighborhood_preferences=neighborhood_prefs,
        neighborhood_avoid=neighborhood_avoid,
        room_configuration=room_configuration,
        special_requirements=special_reqs,
        prompt=prompt,
        purpose="accommodation_options_lookup",
    )

    accommodation_state.search_tasks.append(task)
    save_accommodation_state(tool_context, accommodation_state)

    logger.info(
        "[Tool] derive_accommodation_search_tasks completed",
        extra={
            "destination": accommodation_location,
            "num_travelers": len(travelers),
            "task_id": task_id,
        },
    )

    print(
        f"[Tool] derive_accommodation_search_tasks created 1 accommodation task "
        f"for destination={accommodation_location} travelers={traveler_indexes}"
    )

    return {
        "status": "success",
        "num_tasks_created": 1,
        "tasks": [task.model_dump()],
    }


def apply_flight_search_results(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Apply FlightSearchResult entries back into FlightState by deriving a simple
    overall summary. This is intentionally lightweight and non-LLM: it aggregates
    best price and time hints across tasks so that reporting agents can reference
    a single summary field.
    """
    flight_state = get_flight_state(tool_context)
    planner_state = get_planner_state(tool_context)

    if not flight_state.search_results:
        logger.info("[Tool] apply_flight_search_results skipped – no search_results present")
        return {"status": "skipped", "reason": "no_search_results"}

    lines: List[str] = []
    for result in flight_state.search_results:
        parts: List[str] = []
        if result.summary:
            parts.append(result.summary.strip())
        if result.best_time_hint:
            parts.append(f"Time hint: {result.best_time_hint}")
        if result.recommended_option_label:
            parts.append(f"Recommended: {result.recommended_option_label}")
        line = " ".join(parts)
        if line:
            lines.append(f"- Task {result.task_id}: {line}")

    if lines:
        flight_state.overall_summary = "\n".join(lines)

    # Build per-traveler flight choices so later agents can reason
    # about itineraries without re-implementing the join logic.
    traveler_flights: List[TravelerFlightChoice] = []

    travelers = planner_state.demographics.travelers or []
    results_by_task: Dict[str, FlightSearchResult] = {
        r.task_id: r for r in flight_state.search_results or []
    }

    for traveler_index in range(len(travelers)):
        for task in flight_state.search_tasks or []:
            if traveler_index not in (task.traveler_indexes or []):
                continue

            result = results_by_task.get(task.task_id)
            if result is None:
                continue

            chosen_option = None
            other_options: List[FlightOption] = []

            chosen_type = result.chosen_option_type
            for opt in result.options or []:
                if chosen_type and opt.option_type == chosen_type and chosen_option is None:
                    chosen_option = opt
                else:
                    other_options.append(opt)

            if chosen_option is None and result.options:
                chosen_option = result.options[0]
                other_options = list(result.options[1:])

            traveler_flights.append(
                TravelerFlightChoice(
                    traveler_index=traveler_index,
                    task_id=task.task_id,
                    summary=result.summary,
                    best_price_hint=result.best_price_hint,
                    best_time_hint=result.best_time_hint,
                    cheap_but_long_hint=result.cheap_but_long_hint,
                    recommended_option_label=result.recommended_option_label,
                    notes=result.notes,
                    chosen_option_type=result.chosen_option_type,
                    selection_reason=result.selection_reason,
                    chosen_option=chosen_option,
                    other_options=other_options,
                )
            )

    flight_state.traveler_flights = traveler_flights

    save_flight_state(tool_context, flight_state)

    logger.info(
        "[Tool] apply_flight_search_results completed",
        extra={
            "num_tasks": len(flight_state.search_tasks),
            "num_results": len(flight_state.search_results),
            "num_traveler_flights": len(traveler_flights),
        },
    )

    print("[Tool] apply_flight_search_results updated FlightState.overall_summary")

    return {
        "status": "success",
        "num_tasks": len(flight_state.search_tasks),
        "num_results": len(flight_state.search_results),
        "num_traveler_flights": len(traveler_flights),
    }


def record_accommodation_search_result(
    tool_context: ToolContext,
    task_id: str,
    summary: str,
    options: Optional[List[Dict[str, Any]]] = None,
    best_price_hint: Optional[str] = None,
    best_location_hint: Optional[str] = None,
    family_friendly_hint: Optional[str] = None,
    neighborhood_hint: Optional[str] = None,
    recommended_option_label: Optional[str] = None,
    notes: Optional[str] = None,
    chosen_option_type: Optional[str] = None,
    selection_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist a normalized AccommodationSearchResult into AccommodationState for a given task_id.

    This tool does NOT call external services. It relies on the agent
    to pass in a concise summary and extracted hints based on prior
    accommodation search calls.
    """
    accommodation_state = get_accommodation_state(tool_context)

    matching_task = next(
        (t for t in accommodation_state.search_tasks if t.task_id == task_id),
        None,
    )
    if matching_task is None:
        logger.warning(
            "[Tool] record_accommodation_search_result called with unknown task_id",
            extra={"task_id": task_id},
        )
        return {"status": "error", "reason": "unknown_task_id", "task_id": task_id}

    query = matching_task.prompt

    option_models: List[AccommodationOption] = []
    for opt in options or []:
        try:
            option_models.append(AccommodationOption(**opt))
        except Exception as exc:
            logger.warning(
                "[Tool] record_accommodation_search_result could not parse option",
                extra={"task_id": task_id, "option": opt, "error": str(exc)},
            )

    result = AccommodationSearchResult(
        task_id=task_id,
        query=query,
        options=option_models,
        summary=summary,
        best_price_hint=best_price_hint,
        best_location_hint=best_location_hint,
        family_friendly_hint=family_friendly_hint,
        neighborhood_hint=neighborhood_hint,
        recommended_option_label=recommended_option_label,
        notes=notes,
        chosen_option_type=chosen_option_type,
        selection_reason=selection_reason,
    )

    accommodation_state.search_results.append(result)
    save_accommodation_state(tool_context, accommodation_state)

    logger.info(
        "[Tool] record_accommodation_search_result completed",
        extra={
            "task_id": task_id,
            "num_results_total": len(accommodation_state.search_results),
        },
    )

    print(
        f"[Accommodation Result Tool] Recorded AccommodationSearchResult for task_id={task_id}"
    )

    return {
        "status": "success",
        "task_id": task_id,
        "num_results_total": len(accommodation_state.search_results),
    }


def apply_accommodation_search_results(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Apply AccommodationSearchResult entries back into AccommodationState by deriving
    an overall summary and per-traveler accommodation choices.

    This mirrors apply_flight_search_results but for accommodation.
    """
    accommodation_state = get_accommodation_state(tool_context)
    planner_state = get_planner_state(tool_context)

    if not accommodation_state.search_results:
        logger.info(
            "[Tool] apply_accommodation_search_results skipped – no search_results present",
        )
        return {"status": "skipped", "reason": "no_search_results"}

    lines: List[str] = []
    for result in accommodation_state.search_results:
        parts: List[str] = []
        if result.summary:
            parts.append(result.summary.strip())
        if result.best_price_hint:
            parts.append(f"Price hint: {result.best_price_hint}")
        if result.best_location_hint:
            parts.append(f"Location hint: {result.best_location_hint}")
        if result.recommended_option_label:
            parts.append(f"Recommended: {result.recommended_option_label}")
        line = " ".join(parts)
        if line:
            lines.append(f"- Task {result.task_id}: {line}")

    if lines:
        accommodation_state.overall_summary = "\n".join(lines)

    traveler_accommodations: List[TravelerAccommodationChoice] = []

    travelers = planner_state.demographics.travelers or []
    results_by_task: Dict[str, AccommodationSearchResult] = {
        r.task_id: r for r in accommodation_state.search_results or []
    }

    for traveler_index in range(len(travelers)):
        for task in accommodation_state.search_tasks or []:
            if traveler_index not in (task.traveler_indexes or []):
                continue

            result = results_by_task.get(task.task_id)
            if result is None:
                continue

            chosen_option = None
            other_options: List[AccommodationOption] = []

            chosen_type = result.chosen_option_type
            for opt in result.options or []:
                if chosen_type and opt.option_type == chosen_type and chosen_option is None:
                    chosen_option = opt
                else:
                    other_options.append(opt)

            if chosen_option is None and result.options:
                chosen_option = result.options[0]
                other_options = list(result.options[1:])

            traveler_accommodations.append(
                TravelerAccommodationChoice(
                    traveler_index=traveler_index,
                    task_id=task.task_id,
                    summary=result.summary,
                    best_price_hint=result.best_price_hint,
                    best_location_hint=result.best_location_hint,
                    family_friendly_hint=result.family_friendly_hint,
                    neighborhood_hint=result.neighborhood_hint,
                    recommended_option_label=result.recommended_option_label,
                    notes=result.notes,
                    chosen_option_type=result.chosen_option_type,
                    selection_reason=result.selection_reason,
                    chosen_option=chosen_option,
                    other_options=other_options,
                )
            )

    accommodation_state.traveler_accommodations = traveler_accommodations

    save_accommodation_state(tool_context, accommodation_state)

    logger.info(
        "[Tool] apply_accommodation_search_results completed",
        extra={
            "num_tasks": len(accommodation_state.search_tasks),
            "num_results": len(accommodation_state.search_results),
            "num_traveler_accommodations": len(traveler_accommodations),
        },
    )

    print(
        "[Tool] apply_accommodation_search_results updated AccommodationState.overall_summary"
    )

    return {
        "status": "success",
        "num_tasks": len(accommodation_state.search_tasks),
        "num_results": len(accommodation_state.search_results),
        "num_traveler_accommodations": len(traveler_accommodations),
    }


def record_traveler_accommodation_choice(
    tool_context: ToolContext,
    task_id: str,
    traveler_indexes: List[int],
    chosen_option_type: Optional[
        Literal["cheapest", "best_location", "family_friendly", "balanced", "luxury"]
    ] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record per-traveler accommodation choices for a single AccommodationSearchTask.

    This is an explicit, function-call style helper that an agent can use once it has
    decided which canonical option type should be treated as "chosen" for the given task.

    For each traveler_index in traveler_indexes, a TravelerAccommodationChoice entry is
    appended to AccommodationState.traveler_accommodations.
    """
    accommodation_state = get_accommodation_state(tool_context)
    planner_state = get_planner_state(tool_context)

    matching_task = next(
        (t for t in accommodation_state.search_tasks if t.task_id == task_id),
        None,
    )
    if matching_task is None:
        logger.warning(
            "[Tool] record_traveler_accommodation_choice called with unknown task_id",
            extra={"task_id": task_id},
        )
        return {"status": "error", "reason": "unknown_task_id", "task_id": task_id}

    results_by_task: Dict[str, AccommodationSearchResult] = {
        r.task_id: r for r in accommodation_state.search_results or []
    }
    result = results_by_task.get(task_id)
    if result is None:
        logger.warning(
            "[Tool] record_traveler_accommodation_choice called with no search_result present",
            extra={"task_id": task_id},
        )
        return {
            "status": "error",
            "reason": "no_search_result",
            "task_id": task_id,
        }

    # Decide which option is considered "chosen" for this task.
    chosen_option = None
    other_options: List[AccommodationOption] = []

    effective_type = chosen_option_type or result.chosen_option_type
    for opt in result.options or []:
        if effective_type and opt.option_type == effective_type and chosen_option is None:
            chosen_option = opt
        else:
            other_options.append(opt)

    if chosen_option is None and result.options:
        chosen_option = result.options[0]
        other_options = list(result.options[1:])

    if chosen_option is None:
        logger.warning(
            "[Tool] record_traveler_accommodation_choice found no options to choose from",
            extra={"task_id": task_id},
        )
        return {
            "status": "error",
            "reason": "no_options",
            "task_id": task_id,
        }

    travelers = planner_state.demographics.travelers or []
    valid_indexes = {
        idx for idx in traveler_indexes if 0 <= idx < len(travelers)
    }
    if not valid_indexes:
        logger.warning(
            "[Tool] record_traveler_accommodation_choice received no valid traveler_indexes",
            extra={"task_id": task_id, "traveler_indexes": traveler_indexes},
        )
        return {
            "status": "error",
            "reason": "no_valid_travelers",
            "task_id": task_id,
        }

    created_count = 0
    for traveler_index in sorted(valid_indexes):
        accommodation_state.traveler_accommodations.append(
            TravelerAccommodationChoice(
                traveler_index=traveler_index,
                task_id=task_id,
                summary=result.summary,
                best_price_hint=result.best_price_hint,
                best_location_hint=result.best_location_hint,
                family_friendly_hint=result.family_friendly_hint,
                neighborhood_hint=result.neighborhood_hint,
                recommended_option_label=result.recommended_option_label,
                notes=notes or result.notes,
                chosen_option_type=effective_type,
                selection_reason=result.selection_reason,
                chosen_option=chosen_option,
                other_options=other_options,
            )
        )
        created_count += 1

    save_accommodation_state(tool_context, accommodation_state)

    logger.info(
        "[Tool] record_traveler_accommodation_choice completed",
        extra={
            "task_id": task_id,
            "num_travelers": created_count,
        },
    )

    return {
        "status": "success",
        "task_id": task_id,
        "num_travelers": created_count,
    }


def _build_canonical_accommodation_options(raw_options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Helper to pick up to three canonical accommodation options from the raw
    normalized options list returned by SearchAPI tools.

    We assign:
      - 'cheapest' to the lowest total_price_low (or nightly_price_low)
      - 'best_location' to the highest rating (if distinct)
      - 'balanced' to another reasonable option (if available)
    """
    if not raw_options:
        return []

    def price_key(opt: Dict[str, Any]) -> float:
        total = opt.get("total_price_low") or opt.get("total_price_high")
        nightly = opt.get("nightly_price_low") or opt.get("nightly_price_high")
        return float(total or nightly or 0.0)

    def rating_key(opt: Dict[str, Any]) -> float:
        return float(opt.get("rating") or 0.0)

    sorted_by_price = sorted(raw_options, key=price_key)
    cheapest = sorted_by_price[0]

    sorted_by_rating = sorted(raw_options, key=rating_key, reverse=True)
    best_loc = sorted_by_rating[0] if sorted_by_rating else None

    canonical: List[Dict[str, Any]] = []

    def make_option(opt: Dict[str, Any], option_type: str) -> Dict[str, Any]:
        provider = opt.get("provider")
        stay_type = "vacation_rental" if provider == "airbnb" else "hotel" if provider == "google_hotels" else "other"
        return {
            "option_type": option_type,
            "stay_type": stay_type,
            "provider": provider,
            "name": opt.get("name"),
            "description": opt.get("description"),
            "location_label": opt.get("location_label"),
            "neighborhood": opt.get("neighborhood"),
            "city": opt.get("city"),
            "country": opt.get("country"),
            "currency": opt.get("currency"),
            "nightly_price_low": opt.get("nightly_price_low"),
            "nightly_price_high": opt.get("nightly_price_high"),
            "total_price_low": opt.get("total_price_low"),
            "total_price_high": opt.get("total_price_high"),
            "rating": opt.get("rating"),
            "rating_count": opt.get("rating_count"),
            "max_guests": opt.get("max_guests"),
            "bedrooms": opt.get("bedrooms"),
            "beds": opt.get("beds"),
            "bathrooms": opt.get("bathrooms"),
            "amenities": opt.get("amenities") or [],
            "cancellation_policy": opt.get("cancellation_policy"),
            "url": opt.get("url"),
            "notes": opt.get("notes"),
        }

    canonical.append(make_option(cheapest, "cheapest"))

    if best_loc and best_loc is not cheapest:
        canonical.append(make_option(best_loc, "best_location"))

    for opt in sorted_by_price:
        if len(canonical) >= 3:
            break
        if opt is cheapest or opt is best_loc:
            continue
        canonical.append(make_option(opt, "balanced"))

    return canonical


def read_flights_for_traveler(
    tool_context: ToolContext,
    traveler_index: int,
) -> Dict[str, Any]:
    """
    Helper to expose flight choices for a specific traveler.

    It joins PlannerState and FlightState so agents can see, in one
    object, which FlightSearchTasks cover this traveler and which
    FlightOptions were chosen vs alternatives.

    Returns a dictionary with:
      - traveler_index: the requested index
      - traveler: basic traveler fields from PlannerState (if available)
      - tasks: list of entries, one per matching FlightSearchTask:
          - task_id, origin, destination, traveler_indexes
          - departure_date, return_date
          - summary, best_price_hint, best_time_hint, cheap_but_long_hint
          - recommended_option_label, notes, chosen_option_type, selection_reason
          - chosen_option: the selected FlightOption (dict) if any
          - other_options: remaining FlightOptions (list of dicts)
    """
    planner_state = get_planner_state(tool_context)
    flight_state = get_flight_state(tool_context)

    traveler: Dict[str, Any] | None = None
    travelers = planner_state.demographics.travelers or []
    if 0 <= traveler_index < len(travelers):
        traveler = travelers[traveler_index].model_dump()

    results_by_task: Dict[str, FlightSearchResult] = {
        r.task_id: r for r in flight_state.search_results or []
    }

    tasks_payload: List[Dict[str, Any]] = []

    for task in flight_state.search_tasks or []:
        if traveler_index not in (task.traveler_indexes or []):
            continue

        result = results_by_task.get(task.task_id)
        if result is None:
            # No search result yet for this task; surface basic task info only.
            tasks_payload.append(
                {
                    "task_id": task.task_id,
                    "origin": task.origin_city,
                    "destination": task.destination_city,
                    "traveler_indexes": task.traveler_indexes,
                    "departure_date": task.recommended_departure_date
                    or task.original_departure_date,
                    "return_date": task.recommended_return_date or task.original_return_date,
                    "summary": None,
                    "best_price_hint": None,
                    "best_time_hint": None,
                    "cheap_but_long_hint": None,
                    "recommended_option_label": None,
                    "notes": None,
                    "chosen_option_type": None,
                    "selection_reason": None,
                    "chosen_option": None,
                    "other_options": [],
                }
            )
            continue

        chosen_option = None
        other_options: List[Dict[str, Any]] = []

        chosen_type = result.chosen_option_type
        for opt in result.options or []:
            if chosen_type and opt.option_type == chosen_type and chosen_option is None:
                chosen_option = opt.model_dump()
            else:
                other_options.append(opt.model_dump())

        if chosen_option is None and result.options:
            # Fallback: treat the first option as chosen if none matched.
            first = result.options[0]
            chosen_option = first.model_dump()
            other_options = [opt.model_dump() for opt in result.options[1:]]

        tasks_payload.append(
            {
                "task_id": task.task_id,
                "origin": task.origin_city,
                "destination": task.destination_city,
                "traveler_indexes": task.traveler_indexes,
                "departure_date": task.recommended_departure_date
                or task.original_departure_date,
                "return_date": task.recommended_return_date or task.original_return_date,
                "summary": result.summary,
                "best_price_hint": result.best_price_hint,
                "best_time_hint": result.best_time_hint,
                "cheap_but_long_hint": result.cheap_but_long_hint,
                "recommended_option_label": result.recommended_option_label,
                "notes": result.notes,
                "chosen_option_type": result.chosen_option_type,
                "selection_reason": result.selection_reason,
                "chosen_option": chosen_option,
                "other_options": other_options,
            }
        )

    logger.info(
        "[Tool] read_flights_for_traveler called",
        extra={
            "traveler_index": traveler_index,
            "num_tasks": len(tasks_payload),
        },
    )

    return {
        "traveler_index": traveler_index,
        "traveler": traveler,
        "tasks": tasks_payload,
    }


def searchapi_google_flights(
    tool_context: ToolContext,
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    travel_class: Optional[str] = None,
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call SearchAPI.io's Google Flights engine for structured flight data.

    This complements the generic google_search and skyscanner_search_flights tools
    by returning flight-specific JSON suitable for downstream normalization.

    Environment:
      - SEARCHAPI_IO_API_KEY: API key for https://www.searchapi.io/ (required)

    Args mirror the core query parameters from src/tools/google_flights.yaml.
    """
    api_key = os.getenv("SEARCHAPI_IO_API_KEY")
    if not api_key:
        logger.warning(
            "[Tool] searchapi_google_flights missing SEARCHAPI_IO_API_KEY",
            extra={},
        )
        return {
            "status": "error",
            "reason": "missing_configuration",
            "detail": "SEARCHAPI_IO_API_KEY must be set.",
        }

    base_url = "https://www.searchapi.io/api/v1/search"

    params: Dict[str, Any] = {
        "engine": "google_flights",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "adults": adults,
        "children": children,
        "infants_in_seat": infants_in_seat,
    }
    if return_date:
        params["return_date"] = return_date
    if travel_class:
        # Matches the "travel_class" query parameter in the OpenAPI spec.
        params["travel_class"] = travel_class
    if currency:
        params["currency"] = currency

    # Prefer query-param auth (ApiKeyQuery) to avoid header confusion.
    params["api_key"] = api_key

    try:
        response = requests.get(base_url, params=params, timeout=15)
    except Exception as exc:
        logger.exception(
            "[Tool] searchapi_google_flights request failed",
            extra={"departure_id": departure_id, "arrival_id": arrival_id},
        )
        return {
            "status": "error",
            "reason": "request_failed",
            "detail": str(exc),
        }

    if response.status_code != 200:
        logger.warning(
            "[Tool] searchapi_google_flights non-200 response",
            extra={
                "status_code": response.status_code,
                "text_preview": response.text[:200],
            },
        )
        return {
            "status": "error",
            "reason": "non_200",
            "status_code": response.status_code,
            "body_preview": response.text[:200],
        }

    try:
        raw_json = response.json()
    except ValueError:
        logger.warning(
            "[Tool] searchapi_google_flights invalid JSON response",
            extra={"text_preview": response.text[:200]},
        )
        return {
            "status": "error",
            "reason": "invalid_json",
            "body_preview": response.text[:200],
        }

    # Lightweight normalization for SearchAPI.io's Google Flights schema.
    # Each entry in best_flights/other_flights has:
    #   - price
    #   - total_duration
    #   - flights: list of segments (with airline, departure_airport, arrival_airport, etc.)
    #   - layovers, carbon_emissions, etc.
    options: List[Dict[str, Any]] = []
    if isinstance(raw_json, dict):
        best = raw_json.get("best_flights") or []
        other = raw_json.get("other_flights") or []

        def _build_option(flight: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
            if not isinstance(flight, dict):
                return None

            segments = flight.get("flights") or []
            airlines = sorted(
                {
                    seg.get("airline")
                    for seg in segments
                    if isinstance(seg, dict) and seg.get("airline")
                }
            )

            legs: List[Dict[str, Any]] = []
            total_seg_duration = 0
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                dep = seg.get("departure_airport") or {}
                arr = seg.get("arrival_airport") or {}
                departure_time = None
                if dep.get("date") and dep.get("time"):
                    departure_time = f"{dep.get('date')}T{dep.get('time')}"
                arrival_time = None
                if arr.get("date") and arr.get("time"):
                    arrival_time = f"{arr.get('date')}T{arr.get('time')}"

                seg_duration = seg.get("duration")
                if isinstance(seg_duration, int):
                    total_seg_duration += seg_duration

                legs.append(
                    {
                        "airline": seg.get("airline"),
                        "flight_number": seg.get("flight_number"),
                        "departure_airport": dep.get("id"),
                        "departure_time": departure_time,
                        "arrival_airport": arr.get("id"),
                        "arrival_time": arrival_time,
                        "duration_minutes": seg_duration,
                    }
                )

            total_duration = flight.get("total_duration")
            if not isinstance(total_duration, int):
                total_duration = total_seg_duration or None

            option: Dict[str, Any] = {
                "price": flight.get("price"),
                "airlines": airlines or None,
                "duration_minutes": total_duration,
                "stops": max(len(legs) - 1, 0) if legs else None,
                "legs": legs,
                "total_outbound_duration_minutes": total_duration,
                "total_return_duration_minutes": None,
                "total_trip_duration_minutes": total_duration,
                "source": source,
                "raw": flight,
            }
            return option

        for flight in best:
            opt = _build_option(flight, source="best")
            if opt:
                options.append(opt)
        for flight in other:
            opt = _build_option(flight, source="other")
            if opt:
                options.append(opt)

    print(
        "[Tool DEBUG] searchapi_google_flights options summary:",
        {
            "num_options": len(options),
            "first_option_price": options[0].get("price") if options else None,
            "first_option_airlines": options[0].get("airlines") if options else None,
        },
    )

    logger.info(
        "[Tool] searchapi_google_flights completed",
        extra={
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "return_date": return_date,
            "num_options": len(options),
        },
    )

    return {
        "status": "success",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "adults": adults,
        "children": children,
        "infants_in_seat": infants_in_seat,
        "travel_class": travel_class,
        "currency": currency,
        "num_options": len(options),
        "options": options,
        "raw": raw_json,
    }


def searchapi_google_flights_calendar(
    tool_context: ToolContext,
    departure_id: str,
    arrival_id: str,
    outbound_date_start: str,
    outbound_date_end: str,
    return_date_start: Optional[str] = None,
    return_date_end: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    travel_class: Optional[str] = None,
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call SearchAPI.io's Google Flights Calendar engine for a date range.

    This returns a calendar grid of prices across outbound/return date combinations,
    useful for finding cheaper or nearby dates when the exact requested dates
    are flexible or sold out.

    Environment:
      - SEARCHAPI_IO_API_KEY: API key for https://www.searchapi.io/ (required)
    """
    api_key = os.getenv("SEARCHAPI_IO_API_KEY")
    if not api_key:
        logger.warning(
            "[Tool] searchapi_google_flights_calendar missing SEARCHAPI_IO_API_KEY",
            extra={},
        )
        return {
            "status": "error",
            "reason": "missing_configuration",
            "detail": "SEARCHAPI_IO_API_KEY must be set.",
        }

    base_url = "https://www.searchapi.io/api/v1/search"

    # Use the start dates both as the central outbound/return dates and as the
    # beginning of the explored window, so we satisfy the required fields in
    # the calendar API while still exposing a range.
    params: Dict[str, Any] = {
        "engine": "google_flights_calendar",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date_start,
        "outbound_date_start": outbound_date_start,
        "outbound_date_end": outbound_date_end,
        "adults": adults,
        "children": children,
        "infants_in_seat": infants_in_seat,
    }
    if return_date_start:
        params["return_date"] = return_date_start
        params["return_date_start"] = return_date_start
    if return_date_end:
        params["return_date_end"] = return_date_end
    if travel_class:
        params["travel_class"] = travel_class
    if currency:
        params["currency"] = currency

    params["api_key"] = api_key

    try:
        response = requests.get(base_url, params=params, timeout=15)
    except Exception as exc:
        logger.exception(
            "[Tool] searchapi_google_flights_calendar request failed",
            extra={"departure_id": departure_id, "arrival_id": arrival_id},
        )
        return {
            "status": "error",
            "reason": "request_failed",
            "detail": str(exc),
        }

    if response.status_code != 200:
        logger.warning(
            "[Tool] searchapi_google_flights_calendar non-200 response",
            extra={
                "status_code": response.status_code,
                "text_preview": response.text[:200],
            },
        )
        return {
            "status": "error",
            "reason": "non_200",
            "status_code": response.status_code,
            "body_preview": response.text[:200],
        }

    try:
        raw_json = response.json()
    except ValueError:
        logger.warning(
            "[Tool] searchapi_google_flights_calendar invalid JSON response",
            extra={"text_preview": response.text[:200]},
        )
        return {
            "status": "error",
            "reason": "invalid_json",
            "body_preview": response.text[:200],
        }

    # Normalize the calendar list into a simple array of entries the LLM can reason over.
    calendar_entries: List[Dict[str, Any]] = []
    if isinstance(raw_json, dict):
        for entry in raw_json.get("calendar") or []:
            if not isinstance(entry, dict):
                continue
            calendar_entries.append(
                {
                    "departure": entry.get("departure"),
                    "return": entry.get("return"),
                    "price": entry.get("price"),
                    "has_no_flights": entry.get("has_no_flights"),
                    "is_lowest_price": entry.get("is_lowest_price"),
                }
            )

    logger.info(
        "[Tool] searchapi_google_flights_calendar completed",
        extra={
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date_start": outbound_date_start,
            "outbound_date_end": outbound_date_end,
            "return_date_start": return_date_start,
            "return_date_end": return_date_end,
            "num_entries": len(calendar_entries),
        },
    )

    return {
        "status": "success",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date_start": outbound_date_start,
        "outbound_date_end": outbound_date_end,
        "return_date_start": return_date_start,
        "return_date_end": return_date_end,
        "adults": adults,
        "children": children,
        "infants_in_seat": infants_in_seat,
        "travel_class": travel_class,
        "currency": currency,
        "num_entries": len(calendar_entries),
        "calendar": calendar_entries,
        "raw": raw_json,
    }


def derive_activity_search_tasks(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Build ActivitySearchTask objects based on the current PlannerState.

    For a first iteration, we create a single task that covers the full trip
    window and all travelers, using destination / dates / interests from
    PlannerState.
    """
    planner_state = get_planner_state(tool_context)
    activity_state = get_activity_state(tool_context)

    destination = planner_state.trip_details.destination
    planner_start_date = planner_state.trip_details.start_date
    planner_end_date = planner_state.trip_details.end_date
    travelers = planner_state.demographics.travelers or []

    if not destination or not planner_start_date or not planner_end_date or not travelers:
        logger.info(
            "[Tool] derive_activity_search_tasks skipped – missing destination, dates, or travelers",
        )
        return {
            "status": "skipped",
            "reason": "missing_destination_dates_or_travelers",
        }

    traveler_indexes = list(range(len(travelers)))

    # Default to planner start/end dates, but if we have concrete flight choices
    # with arrival/departure datetimes, tighten the activity window to the
    # actual on-the-ground days.
    date_start = planner_start_date
    date_end = planner_end_date

    flight_state = get_flight_state(tool_context)
    arrival_dates: List[str] = []
    departure_dates: List[str] = []

    for choice in flight_state.traveler_flights or []:
        if choice.traveler_index not in traveler_indexes:
            continue

        option = choice.chosen_option
        if option is None and choice.other_options:
            option = choice.other_options[0]
        if option is None:
            continue

        if option.outbound_arrival and len(option.outbound_arrival) >= 10:
            arrival_dates.append(option.outbound_arrival[:10])

        dep_str = option.return_departure or option.return_arrival
        if dep_str and len(dep_str) >= 10:
            departure_dates.append(dep_str[:10])

    if arrival_dates:
        date_start = min(arrival_dates)
    if departure_dates:
        date_end = max(departure_dates)

    # Guard against inverted activity windows (e.g. arrival after planner end date).
    if (
        date_start
        and date_end
        and date_start > date_end
    ):
        if planner_start_date and planner_end_date and planner_start_date <= planner_end_date:
            date_start = planner_start_date
            date_end = planner_end_date
        else:
            date_end = date_start
    pref = planner_state.preferences

    interests = pref.interests or []
    must_do = pref.must_do or []
    nice_to_have = pref.nice_to_have or []

    task_id = f"activities_{len(activity_state.search_tasks)}"

    prompt_lines: List[str] = [
        "Search for typical activities, attractions, and food experiences for the following trip context:",
        f"- Destination: {destination}",
        f"- Dates: {date_start or planner_start_date} to {date_end or planner_end_date}",
        f"- Travelers covered (indexes): {traveler_indexes}",
        f"- Budget mode: {pref.budget_mode or 'unspecified'}",
    ]

    if interests:
        prompt_lines.append(f"- Interests: {interests}")
    if must_do:
        prompt_lines.append(f"- Must-do items: {must_do}")
    if nice_to_have:
        prompt_lines.append(f"- Nice-to-have themes: {nice_to_have}")
    if pref.daily_rhythm:
        prompt_lines.append(f"- Daily rhythm: {pref.daily_rhythm}")
    if pref.mobility_constraints:
        prompt_lines.append(f"- Mobility constraints: {pref.mobility_constraints}")

    prompt_lines.append(
        "- Focus on activities that would realistically fit into a family-friendly itinerary. "
        "Include indoor and outdoor options, and a mix of paid and free experiences where possible."
    )

    prompt = "\n".join(prompt_lines)

    task = ActivitySearchTask(
        task_id=task_id,
        traveler_indexes=traveler_indexes,
        location=destination,
        date_start=date_start,
        date_end=date_end,
        interests=list(interests),
        must_do=list(must_do),
        nice_to_have=list(nice_to_have),
        budget_mode=pref.budget_mode,
        prompt=prompt,
        purpose="activity_options_lookup",
    )

    activity_state.search_tasks.append(task)
    save_activity_state(tool_context, activity_state)

    logger.info(
        "[Tool] derive_activity_search_tasks completed",
        extra={
            "destination": destination,
            "start_date": date_start,
            "end_date": date_end,
            "task_id": task_id,
        },
    )

    print(
        f"[Tool] derive_activity_search_tasks created 1 activity task "
        f"for destination={destination} travelers={traveler_indexes}"
    )

    return {
        "status": "success",
        "task_id": task_id,
    }


def record_activity_search_result(
    tool_context: ToolContext,
    task_id: str,
    summary: str,
    options: Optional[List[Dict[str, Any]]] = None,
    budget_hint: Optional[str] = None,
    family_friendly_hint: Optional[str] = None,
    neighborhood_hint: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist a normalized ActivitySearchResult into ActivityState for a given task_id.

    This tool is intended to be used by an activity-focused agent that has
    called google_search (or other tools) and normalized the raw results.
    """
    activity_state = get_activity_state(tool_context)

    matching_task = next(
        (t for t in activity_state.search_tasks if t.task_id == task_id),
        None,
    )
    if matching_task is None:
        logger.warning(
            "[Tool] record_activity_search_result called with unknown task_id",
            extra={"task_id": task_id},
        )
        return {"status": "error", "reason": "unknown_task_id", "task_id": task_id}

    option_models: List[ActivityOption] = []
    for opt in options or []:
        if not isinstance(opt, dict):
            continue
        try:
            option_models.append(ActivityOption(**opt))
        except Exception as exc:
            logger.warning(
                "[Tool] record_activity_search_result could not parse option",
                extra={"task_id": task_id, "option": opt, "error": str(exc)},
            )

    result = ActivitySearchResult(
        task_id=task_id,
        query=query,
        options=option_models,
        summary=summary,
        budget_hint=budget_hint,
        family_friendly_hint=family_friendly_hint,
        neighborhood_hint=neighborhood_hint,
    )

    activity_state.search_results.append(result)
    save_activity_state(tool_context, activity_state)

    logger.info(
        "[Tool] record_activity_search_result completed",
        extra={
            "task_id": task_id,
            "num_results_total": len(activity_state.search_results),
        },
    )

    print(f"[Activity Result Tool] Recorded ActivitySearchResult for task_id={task_id}")

    return {
        "status": "success",
        "task_id": task_id,
        "num_results_total": len(activity_state.search_results),
    }


def apply_activity_search_results(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Apply ActivitySearchResult entries back into ActivityState by deriving
    a coarse day-by-day itinerary.

    This is intentionally simple: it spreads activities across the trip days
    and fills morning / afternoon / evening slots where possible.
    """
    activity_state = get_activity_state(tool_context)
    planner_state = get_planner_state(tool_context)

    if not activity_state.search_results:
        logger.info(
            "[Tool] apply_activity_search_results skipped – no search_results present",
        )
        return {"status": "skipped", "reason": "no_search_results"}

    # Derive the date range for the itinerary. Prefer the first ActivitySearchTask's
    # date_start/date_end when available; otherwise fall back to the trip dates.
    start_date_str: Optional[str] = None
    end_date_str: Optional[str] = None
    if activity_state.search_tasks:
        first_task = activity_state.search_tasks[0]
        start_date_str = first_task.date_start
        end_date_str = first_task.date_end
    start_date_str = start_date_str or planner_state.trip_details.start_date
    end_date_str = end_date_str or planner_state.trip_details.end_date

    days: List[str] = []
    if start_date_str and end_date_str:
        try:
            start_dt = date.fromisoformat(start_date_str)
            end_dt = date.fromisoformat(end_date_str)
            current = start_dt
            while current <= end_dt:
                days.append(current.isoformat())
                current = current.fromordinal(current.toordinal() + 1)
        except Exception:
            # If date parsing fails, fall back to a single pseudo-day.
            days = [start_date_str]
    else:
        days = [start_date_str or "0000-00-00"]

    # Flatten all options across tasks.
    all_options: List[ActivityOption] = []
    for result in activity_state.search_results:
        all_options.extend(result.options or [])

    if not all_options:
        logger.info(
            "[Tool] apply_activity_search_results found no options to schedule",
        )
        return {"status": "skipped", "reason": "no_options"}

    # Simple round-robin assignment of activities to (day, slot).
    slots: List[str] = ["morning", "afternoon", "evening"]
    items: List[DayItineraryItem] = []

    traveler_indexes = list(range(len(planner_state.demographics.travelers or [])))

    opt_index = 0
    for day in days:
        for slot in slots:
            if opt_index >= len(all_options):
                break
            opt = all_options[opt_index]
            opt_index += 1

            items.append(
                DayItineraryItem(
                    date=day,
                    slot=slot,  # type: ignore[arg-type]
                    traveler_indexes=traveler_indexes,
                    task_id="*",
                    activity=opt,
                    notes=None,
                )
            )

    activity_state.day_plan = items

    lines: List[str] = []
    for item in items:
        lines.append(f"{item.date} {item.slot}: {item.activity.name}")
    if lines:
        activity_state.overall_summary = "\n".join(lines)

    save_activity_state(tool_context, activity_state)

    logger.info(
        "[Tool] apply_activity_search_results completed",
        extra={
            "num_tasks": len(activity_state.search_tasks),
            "num_results": len(activity_state.search_results),
            "num_itinerary_items": len(items),
        },
    )

    print("[Tool] apply_activity_search_results updated ActivityState.day_plan")

    return {
        "status": "success",
        "num_tasks": len(activity_state.search_tasks),
        "num_results": len(activity_state.search_results),
        "num_itinerary_items": len(items),
    }


def record_day_itinerary(
    tool_context: ToolContext,
    items: List[Dict[str, Any]],
    overall_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist a day-by-day itinerary into ActivityState.day_plan.

    This function APPENDS new items to any existing ActivityState.day_plan
    entries instead of replacing them, so that callers can build itineraries
    incrementally (for example, planning a few days at a time).

    To keep things simple and robust for LLMs, each element in `items` should
    be a small object with just the key details:

      - date: ISO date string (required)
      - slot: 'morning' | 'afternoon' | 'evening' (required)
      - task_id: string (optional, '*' is fine if ambiguous)
      - name: short name of the activity, e.g. "Hyde Park Winter Wonderland" (required)
      - notes: optional short string with any important details

    You may optionally include:
      - traveler_indexes: list[int]      (if omitted, all travelers are used)
      - neighborhood: string             (optional)
      - city: string                     (optional)
      - url: string                      (optional)

    The tool will turn each item into a DayItineraryItem + ActivityOption, and
    will not attempt to look anything up from search_results.
    """
    planner_state = get_planner_state(tool_context)
    activity_state = get_activity_state(tool_context)

    # Start from any existing day_plan so multiple calls can add to it.
    existing_items: List[DayItineraryItem] = list(activity_state.day_plan or [])
    new_items: List[DayItineraryItem] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            logger.warning(
                "[Tool] record_day_itinerary could not parse item (not a dict)",
                extra={"item": raw},
            )
            continue
        try:
            date_str = raw.get("date")
            slot_raw = raw.get("slot")
            name = raw.get("name") or raw.get("title") or raw.get("label")

            if not isinstance(date_str, str) or not isinstance(slot_raw, str):
                raise ValueError("missing or invalid date/slot")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("missing activity name")

            slot_normalized = slot_raw.strip().lower()
            if slot_normalized not in ("morning", "afternoon", "evening"):
                raise ValueError(f"invalid slot value: {slot_raw!r}")

            task_id = raw.get("task_id") or "*"

            traveler_indexes = raw.get("traveler_indexes")
            if not traveler_indexes:
                traveler_indexes = list(range(len(planner_state.demographics.travelers or [])))

            activity_model = ActivityOption(
                name=name.strip(),
                category=raw.get("category"),
                location_label=raw.get("location_label"),
                neighborhood=raw.get("neighborhood"),
                city=raw.get("city"),
                country=raw.get("country"),
                url=raw.get("url"),
                notes=raw.get("notes"),
            )

            item = DayItineraryItem(
                date=date_str,
                slot=slot_normalized,  # type: ignore[arg-type]
                traveler_indexes=list(traveler_indexes),
                task_id=task_id,
                activity=activity_model,
                notes=raw.get("notes"),
            )
            new_items.append(item)
        except Exception as exc:
            logger.warning(
                "[Tool] record_day_itinerary could not parse item",
                extra={"item": raw, "error": str(exc)},
            )

    all_items = existing_items + new_items
    activity_state.day_plan = all_items

    if overall_summary is not None:
        activity_state.overall_summary = overall_summary
    else:
        lines: List[str] = []
        for item in all_items:
            lines.append(f"{item.date} {item.slot}: {item.activity.name}")
        if lines:
            activity_state.overall_summary = "\n".join(lines)

    save_activity_state(tool_context, activity_state)

    logger.info(
        "[Tool] record_day_itinerary completed",
        extra={
            "num_items": len(new_items),
        },
    )

    print("[Itinerary Tool] Recorded day-by-day itinerary into ActivityState.day_plan")

    return {
        "status": "success",
        "num_itinerary_items": len(new_items),
    }


def resolve_airports(
    tool_context: ToolContext,
    location: str,
) -> Dict[str, Any]:
    """
    Resolve a free-form location string (e.g. "Houston, Texas", "Lagos")
    into likely airport candidates using SearchAPI.io's Google Flights engine.

    This is intended for use by intake/dispatcher agents before flight planning,
    so that FlightSearchTasks can be built with concrete airport codes.
    """
    api_key = os.getenv("SEARCHAPI_IO_API_KEY")
    if not api_key:
        logger.warning(
            "[Tool] resolve_airports missing SEARCHAPI_IO_API_KEY",
            extra={},
        )
        return {
            "status": "error",
            "reason": "missing_configuration",
            "detail": "SEARCHAPI_IO_API_KEY must be set.",
            "location": location,
        }

    base_url = "https://www.searchapi.io/api/v1/search"

    # Use the google_flights engine with the free-form location as departure_id.
    # We provide a dummy arrival_id and future date just to retrieve the airports list.
    future = date.today().replace(year=date.today().year + 1)
    params: Dict[str, Any] = {
        "engine": "google_flights",
        "departure_id": location,
        "arrival_id": "LHR",
        "outbound_date": future.isoformat(),
        "api_key": api_key,
    }

    try:
        response = requests.get(base_url, params=params, timeout=15)
    except Exception as exc:
        logger.exception(
            "[Tool] resolve_airports request failed",
            extra={"location": location},
        )
        return {
            "status": "error",
            "reason": "request_failed",
            "detail": str(exc),
            "location": location,
        }

    if response.status_code != 200:
        logger.warning(
            "[Tool] resolve_airports non-200 response",
            extra={
                "status_code": response.status_code,
                "text_preview": response.text[:200],
            },
        )
        return {
            "status": "error",
            "reason": "non_200",
            "status_code": response.status_code,
            "body_preview": response.text[:200],
            "location": location,
        }

    try:
        raw_json = response.json()
    except ValueError:
        logger.warning(
            "[Tool] resolve_airports invalid JSON response",
            extra={"text_preview": response.text[:200]},
        )
        return {
            "status": "error",
            "reason": "invalid_json",
            "body_preview": response.text[:200],
            "location": location,
        }

    candidates: List[Dict[str, Any]] = []
    airports = []
    if isinstance(raw_json, dict):
        airports = raw_json.get("airports") or []

    for ap in airports:
        if not isinstance(ap, dict):
            continue
        candidates.append(
            {
                "code": ap.get("code"),
                "name": ap.get("name"),
                "city": ap.get("city"),
                "country": ap.get("country"),
            }
        )

    logger.info(
        "[Tool] resolve_airports completed",
        extra={
            "location": location,
            "num_candidates": len(candidates),
        },
    )

    return {
        "status": "success",
        "location": location,
        "num_candidates": len(candidates),
        "candidates": candidates,
    }


def searchapi_airbnb_properties(
    tool_context: ToolContext,
    location_query: str,
    check_in_date: Optional[str] = None,
    check_out_date: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    pets: int = 0,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    time_period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call SearchAPI.io's Airbnb engine for structured accommodation data.

    This uses the Airbnb OpenAPI spec (src/tools/airbnb_openapi_spec.yaml) and
    returns a lightweight normalized list of options suitable for feeding into
    AccommodationOption via an agent.

    Environment:
      - SEARCHAPI_IO_API_KEY: API key for https://www.searchapi.io/ (required)
    """
    api_key = os.getenv("SEARCHAPI_IO_API_KEY")
    if not api_key:
        logger.warning(
            "[Tool] searchapi_airbnb_properties missing SEARCHAPI_IO_API_KEY",
            extra={},
        )
        return {
            "status": "error",
            "reason": "missing_configuration",
            "detail": "SEARCHAPI_IO_API_KEY must be set.",
        }

    base_url = "https://www.searchapi.io/api/v1/search"

    params: Dict[str, Any] = {
        "engine": "airbnb",
        "q": location_query,
    }
    if check_in_date:
        params["check_in_date"] = check_in_date
    if check_out_date:
        params["check_out_date"] = check_out_date
    if time_period and not check_in_date:
        params["time_period"] = time_period
    if adults:
        params["adults"] = adults
    if children:
        params["children"] = children
    if infants:
        params["infants"] = infants
    if pets:
        params["pets"] = pets
    if min_price is not None:
        params["price_min"] = min_price
    if max_price is not None:
        params["price_max"] = max_price

    params["api_key"] = api_key

    try:
        response = requests.get(base_url, params=params, timeout=15)
    except Exception as exc:
        logger.exception(
            "[Tool] searchapi_airbnb_properties request failed",
            extra={"location_query": location_query},
        )
        return {
            "status": "error",
            "reason": "request_failed",
            "detail": str(exc),
        }

    if response.status_code != 200:
        logger.warning(
            "[Tool] searchapi_airbnb_properties non-200 response",
            extra={
                "status_code": response.status_code,
                "text_preview": response.text[:200],
            },
        )
        return {
            "status": "error",
            "reason": "non_200",
            "status_code": response.status_code,
            "body_preview": response.text[:200],
        }

    try:
        raw_json = response.json()
    except ValueError:
        logger.warning(
            "[Tool] searchapi_airbnb_properties invalid JSON response",
            extra={"text_preview": response.text[:200]},
        )
        return {
            "status": "error",
            "reason": "invalid_json",
            "body_preview": response.text[:200],
        }

    # Lightweight normalization: extract key fields from Airbnb listings into a
    # shape the LLM can easily map to AccommodationOption.
    options: List[Dict[str, Any]] = []
    if isinstance(raw_json, dict):
        listings = raw_json.get("properties") or raw_json.get("results") or []
        for prop in listings:
            if not isinstance(prop, dict):
                continue

            price_info = prop.get("price") or {}
            accommodations = prop.get("accommodations") or []

            # Very coarse currency inference based on the leading symbol.
            currency: Optional[str] = None
            total_price_str = price_info.get("total_price")
            if isinstance(total_price_str, str) and total_price_str.startswith("$"):
                currency = "USD"

            nightly = price_info.get("extracted_price_per_qualifier")
            total = price_info.get("extracted_total_price")

            # Basic bedroom/bed inference from the accommodations strings.
            bedrooms = None
            beds = None
            for item in accommodations:
                if not isinstance(item, str):
                    continue
                lower = item.lower()
                if "bedroom" in lower and bedrooms is None:
                    # e.g. "2 bedrooms"
                    parts = lower.split()
                    for p in parts:
                        if p.isdigit():
                            bedrooms = int(p)
                            break
                if "bed" in lower and beds is None:
                    # e.g. "3 beds" or "2 king beds"
                    parts = lower.split()
                    for p in parts:
                        if p.isdigit():
                            beds = int(p)
                            break

            options.append(
                {
                    "provider": "airbnb",
                    "name": prop.get("title"),
                    "description": prop.get("description"),
                    "location_label": prop.get("description"),
                    "neighborhood": None,
                    "city": None,
                    "country": None,
                    "currency": currency,
                    "nightly_price_low": nightly,
                    "nightly_price_high": nightly,
                    "total_price_low": total,
                    "total_price_high": total,
                    "rating": prop.get("rating"),
                    "rating_count": prop.get("reviews"),
                    "max_guests": None,
                    "bedrooms": bedrooms,
                    "beds": beds,
                    "bathrooms": None,
                    "amenities": accommodations,
                    "url": prop.get("booking_link") or prop.get("link"),
                    "raw": prop,
                }
            )

    logger.info(
        "[Tool] searchapi_airbnb_properties completed",
        extra={
            "location_query": location_query,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "num_options": len(options),
        },
    )

    return {
        "status": "success",
        "engine": "airbnb",
        "location_query": location_query,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "adults": adults,
        "children": children,
        "infants": infants,
        "pets": pets,
        "num_options": len(options),
        "options": options,
        "raw": raw_json,
    }


def searchapi_google_hotels_properties(
    tool_context: ToolContext,
    location_query: str,
    check_in_date: Optional[str] = None,
    check_out_date: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call SearchAPI.io's Google Hotels engine for structured accommodation data.

    This is a complementary source to Airbnb and is intended for hotel-style
    properties. It returns a lightweight normalized list of options that an
    agent can map into AccommodationOption.

    Environment:
      - SEARCHAPI_IO_API_KEY: API key for https://www.searchapi.io/ (required)
    """
    api_key = os.getenv("SEARCHAPI_IO_API_KEY")
    if not api_key:
        logger.warning(
            "[Tool] searchapi_google_hotels_properties missing SEARCHAPI_IO_API_KEY",
            extra={},
        )
        return {
            "status": "error",
            "reason": "missing_configuration",
            "detail": "SEARCHAPI_IO_API_KEY must be set.",
        }

    base_url = "https://www.searchapi.io/api/v1/search"

    # Use SearchAPI.io's Google Hotels engine so hotel queries succeed.
    params: Dict[str, Any] = {
        "engine": "google_hotels",
        "q": location_query,
    }
    if check_in_date:
        params["check_in_date"] = check_in_date
    if check_out_date:
        params["check_out_date"] = check_out_date
    if adults:
        params["adults"] = adults
    if children:
        params["children"] = children
    if min_price is not None:
        params["price_min"] = min_price
    if max_price is not None:
        params["price_max"] = max_price
    if currency:
        params["currency"] = currency

    params["api_key"] = api_key

    try:
        response = requests.get(base_url, params=params, timeout=15)
    except Exception as exc:
        logger.exception(
            "[Tool] searchapi_google_hotels_properties request failed",
            extra={"location_query": location_query},
        )
        return {
            "status": "error",
            "reason": "request_failed",
            "detail": str(exc),
        }

    if response.status_code != 200:
        logger.warning(
            "[Tool] searchapi_google_hotels_properties non-200 response",
            extra={
                "status_code": response.status_code,
                "text_preview": response.text[:200],
            },
        )
        return {
            "status": "error",
            "reason": "non_200",
            "status_code": response.status_code,
            "body_preview": response.text[:200],
        }

    try:
        raw_json = response.json()
    except ValueError:
        logger.warning(
            "[Tool] searchapi_google_hotels_properties invalid JSON response",
            extra={"text_preview": response.text[:200]},
        )
        return {
            "status": "error",
            "reason": "invalid_json",
            "body_preview": response.text[:200],
        }

    options: List[Dict[str, Any]] = []
    if isinstance(raw_json, dict):
        hotels = raw_json.get("hotels") or raw_json.get("properties") or raw_json.get("results") or []
        for hotel in hotels:
            if not isinstance(hotel, dict):
                continue

            # SearchAPI.io Google Hotels responses can expose pricing either under a
            # generic "pricing" object or as "price_per_night"/"total_price" blocks.
            pricing = hotel.get("pricing") or {}
            price_per_night = hotel.get("price_per_night") or {}
            total_price = hotel.get("total_price") or {}

            currency_value = (
                pricing.get("currency")
                or price_per_night.get("currency")
                or currency
            )

            nightly = (
                price_per_night.get("extracted_price")
                or pricing.get("price")
                or price_per_night.get("price")
            )
            total = (
                total_price.get("extracted_price")
                or pricing.get("total_price")
                or nightly
            )

            # Location fields may be nested under "location" or surfaced at the top level.
            location = hotel.get("location") or {}
            city = location.get("city") or hotel.get("city")
            country = location.get("country") or hotel.get("country")
            neighborhood = location.get("neighborhood")
            location_label = neighborhood or location.get("address") or city

            options.append(
                {
                    "provider": "google_hotels",
                    "name": hotel.get("name"),
                    "description": hotel.get("description"),
                    "location_label": location_label,
                    "neighborhood": neighborhood,
                    "city": city,
                    "country": country,
                    "currency": currency_value,
                    "nightly_price_low": nightly,
                    "nightly_price_high": nightly,
                    "total_price_low": total,
                    "total_price_high": total,
                    "rating": hotel.get("rating"),
                    # Some schemas use "reviews" instead of "rating_count".
                    "rating_count": hotel.get("rating_count") or hotel.get("reviews"),
                    "max_guests": hotel.get("max_guests"),
                    "amenities": hotel.get("amenities") or [],
                    "url": hotel.get("link") or hotel.get("url"),
                    "raw": hotel,
                }
            )

    logger.info(
        "[Tool] searchapi_google_hotels_properties completed",
        extra={
            "location_query": location_query,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "num_options": len(options),
        },
    )

    return {
        "status": "success",
        "engine": "google_hotels",
        "location_query": location_query,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "adults": adults,
        "children": children,
        "currency": currency,
        "num_options": len(options),
        "options": options,
        "raw": raw_json,
    }


def skyscanner_search_flights(
    tool_context: ToolContext,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
    cabin: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call the Skyscanner API to search for flights.

    This is a thin wrapper around the HTTP API and is intended to be used
    by a flight-focused agent. It returns the raw JSON response plus a
    lightweight normalized view of options.

    Environment / configuration:
      - SKYSCANNER_API_KEY: API key or bearer token for Skyscanner.
      - SKYSCANNER_FLIGHTS_URL: Base URL for the flights search endpoint.

    Args:
        tool_context: ToolContext provided by ADK (unused except for logging).
        origin: Origin city or airport code (e.g. "LOS" or "Lagos").
        destination: Destination city or airport code (e.g. "LHR" or "London").
        departure_date: Departure date in ISO format (YYYY-MM-DD).
        return_date: Optional return date in ISO format.
        adults: Number of adult passengers.
        cabin: Preferred cabin (e.g. "economy", "premium", "business", "first").

    Returns:
        dict: A dict containing status, request parameters, and either parsed
              results or an error message.
    """
    api_key = os.getenv("SKYSCANNER_API_KEY")
    base_url = os.getenv("SKYSCANNER_FLIGHTS_URL")

    if not api_key or not base_url:
        logger.warning(
            "[Tool] skyscanner_search_flights missing configuration",
            extra={"has_api_key": bool(api_key), "has_base_url": bool(base_url)},
        )
        return {
            "status": "error",
            "reason": "missing_configuration",
            "detail": "SKYSCANNER_API_KEY and SKYSCANNER_FLIGHTS_URL must be set.",
        }

    params: Dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": adults,
    }
    if return_date:
        params["return_date"] = return_date
    if cabin:
        params["cabin_class"] = cabin

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }

    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=10)
    except Exception as exc:
        logger.exception(
            "[Tool] skyscanner_search_flights request failed",
            extra={"origin": origin, "destination": destination},
        )
        return {
            "status": "error",
            "reason": "request_failed",
            "detail": str(exc),
        }

    if response.status_code != 200:
        logger.warning(
            "[Tool] skyscanner_search_flights non-200 response",
            extra={
                "status_code": response.status_code,
                "text_preview": response.text[:200],
            },
        )
        return {
            "status": "error",
            "reason": "non_200",
            "status_code": response.status_code,
            "body_preview": response.text[:200],
        }

    try:
        raw_json = response.json()
    except ValueError:
        logger.warning(
            "[Tool] skyscanner_search_flights invalid JSON response",
            extra={"text_preview": response.text[:200]},
        )
        return {
            "status": "error",
            "reason": "invalid_json",
            "body_preview": response.text[:200],
        }

    # Lightweight normalization: this will need to be adapted to the actual Skyscanner
    # response schema, but we expose a generic "options" list so the LLM can reason over it.
    options: List[Dict[str, Any]] = []
    if isinstance(raw_json, dict):
        # Example heuristic: look for a top-level "itineraries" or "data" list.
        itineraries = raw_json.get("itineraries") or raw_json.get("data") or []
        if isinstance(itineraries, list):
            for item in itineraries:
                if not isinstance(item, dict):
                    continue
                option: Dict[str, Any] = {
                    "price": item.get("price") or item.get("pricing_options"),
                    "duration": item.get("duration"),
                    "stops": item.get("stops"),
                    "carrier": item.get("carrier") or item.get("airline"),
                    "raw": item,
                }
                options.append(option)

    logger.info(
        "[Tool] skyscanner_search_flights completed",
        extra={
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "num_options": len(options),
        },
    )

    return {
        "status": "success",
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "adults": adults,
        "cabin": cabin,
        "num_options": len(options),
        "options": options,
        "raw": raw_json,
    }


def build_visa_search_prompt(
    tool_context: ToolContext,
    traveler_index: int,
    role: str,
    nationality: Optional[str] = None,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    purpose: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construct a clear, templated prompt describing what we intend to
    search for later for a single traveler.

    This does not call any external services. It is purely about
    building and logging a well-structured prompt that a future
    search-focused agent can use.

    Args:
        tool_context: ToolContext provided by ADK.
        traveler_index: Index of the traveler in PlannerState.demographics.travelers.
        role: Role of the traveler (e.g. "adult", "child").
        nationality: Nationality of the traveler (e.g. "Nigerian").
        origin: Origin country of the traveler (e.g. "Nigeria").
        destination: Destination country of the trip (e.g. "UK").
        purpose: Short free-text description of the purpose of the visa search.

    Returns:
        dict: The constructed prompt along with metadata.
    """
    invocation_ctx = getattr(tool_context, "_invocation_context", None)
    app_name = getattr(invocation_ctx, "app_name", None)
    user_id = getattr(tool_context, "user_id", None)

    nationality_display = nationality or "UNKNOWN"
    origin_display = origin or "UNKNOWN ORIGIN"
    destination_display = destination or "UNKNOWN DESTINATION"

    if not purpose:
        purpose = (
            f"visa_requirements_lookup for {nationality_display} traveler "
            f"going from {origin_display} to {destination_display}"
        )

    prompt = (
        "You are an expert at generating visa requirements, costs, required documents, "
        "processing timelines, and any health-related entry conditions (such as mandatory "
        "or recommended vaccinations or medical tests). Only use official government or "
        "approved visa application centre websites so that guidance is up to date.\n\n"
        f"Traveler context:\n"
        f"- Traveler index: {traveler_index}\n"
        f"- Role: {role}\n"
        f"- Nationality: {nationality_display}\n"
        f"- Origin: {origin_display}\n"
        f"- Destination: {destination_display}\n"
        f"- Visa purpose: {purpose}\n\n"
        "Later, another agent will use this prompt to search for:\n"
        "- Whether a visa is required for this traveler.\n"
        "- Recommended visa type.\n"
        "- Typical processing time and approximate fees.\n"
        "- Key supporting documents.\n"
        "- Any health-related entry requirements (e.g. mandatory or recommended vaccines, "
        "medical tests, or health insurance conditions).\n"
        "- Where and how to apply."
    )

    logger.info(
        "[Tool] build_visa_search_prompt called",
        extra={
            "app_name": app_name,
            "user_id": user_id,
            "traveler_index": traveler_index,
            "role": role,
            "nationality": nationality,
            "origin": origin,
            "destination": destination,
            "purpose": purpose,
        },
    )

    # Persist as a VisaSearchTask so that downstream agents can operate
    # over a structured list of tasks.
    visa_state = get_visa_state(tool_context)
    task_id = f"traveler_{traveler_index}_{destination_display}"
    task = VisaSearchTask(
        task_id=task_id,
        traveler_indexes=[traveler_index],
        origin_country=origin,
        destination_country=destination,
        nationality=nationality,
        travel_purpose="tourism",
        prompt=prompt,
        purpose=purpose,
    )
    visa_state.search_tasks.append(task)
    save_visa_state(tool_context, visa_state)

    logger.info(
        "[Tool] build_visa_search_prompt stored VisaSearchTask",
        extra={
            "task_id": task_id,
            "traveler_index": traveler_index,
            "destination": destination,
            "num_search_tasks": len(visa_state.search_tasks),
        },
    )

    print(
        f"[Visa Prompt Tool] Stored VisaSearchTask #{len(visa_state.search_tasks)} for "
        f"traveler_index={traveler_index}, role={role}, "
        f"nationality={nationality_display}, origin={origin_display}, "
        f"destination={destination_display}"
    )

    return {
        "traveler_index": traveler_index,
        "role": role,
        "nationality": nationality,
        "origin": origin,
        "destination": destination,
        "purpose": purpose,
        "task_id": task_id,
        "prompt": prompt,
    }






# def update_trip_plan(
#         tool_context: ToolContext,
#         destination: Optional[str] = None,
#         origin: Optional[str] = None,
#         budget_mode: Optional[str] = None,
#         adults: Optional[int] = None
# ):
#     """
#     Updates the user's trip itinerary in the session state.
#     Call this when the user provides details about their trip
#     such as destination, origin, budget mode, or number of adults.

#     Args:
#         tool_context (ToolContext): The context of the tool call, including session state.
#         destination (Optional[str]): New trip destination.
#         origin (Optional[str]): New trip origin.
#         budget_mode (Optional[str]): New budget mode ("economy", "standard", "luxury").
#         adults (Optional[int]): Number of adult travelers.
    
#     Returns:
#         dict: Confirmation of updated trip details.
#     """

#     #Access tje session state directly
#     state = dict(tool_context.session.state or {})

#     trip_details = dict(state.get("trip_details", {}))
#     demographics = dict(state.get("demographics", {}))
#     preferences = dict(state.get("preferences", {}))

#     print("[Tool] Updating trip plan with provided details...")
#     print(f"Destination: {destination}, Origin: {origin}, Adults: {adults}, Budget Mode: {budget_mode}")
#     print(f"[Tool] Current State before update: {tool_context}")

#     #update fields if provided
#     if destination: state["trip_details"]["destination"] = destination
#     if origin: state["trip_details"]["origin"] = origin
#     if adults: state["demographics"]["adults"] = adults
#     if budget_mode:
#         state["preferences"]["budget_mode"] = budget_mode
#         if budget_mode == "luxury":
#             state["preferences"]["total_budget"] = None
    
#     state["trip_details"] = trip_details
#     state["demographics"] = demographics
#     state["preferences"] = preferences
#     tool_context.session.state = state  # <- this is the key line

#     print(f"[Tool] State after update: {state}")

#     return {"status": "success", "updated_state": state}
