from typing import Optional, List, Dict, Any, Tuple
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
            f"- Travelers covered (indexes): {indexes}\n\n"
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
        if result.best_price_hint:
            parts.append(f"Price hint: {result.best_price_hint}")
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
        "and processing timelines. Only use official government or approved visa "
        "application centre websites so that guidance is up to date.\n\n"
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
