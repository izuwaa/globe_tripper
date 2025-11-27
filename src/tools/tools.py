from typing import Optional, List, Dict, Any, Tuple
import logging
import re
from datetime import date, timedelta
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
from src.state.flight_state import FlightState, FlightSearchTask, FlightSearchResult


logger = logging.getLogger(__name__)


def update_trip_plan(
    tool_context: ToolContext,
    # TripDetails
    destination: Optional[str] = None,
    origin: Optional[str] = None,
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

    # ---- Status / lifecycle ----
    if state.status == "intake" and is_intake_complete(state):
        state.status = "planning"

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

    destination = planner_state.trip_details.destination
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

    destination = planner_state.trip_details.destination
    origin_default = planner_state.trip_details.origin
    start_date = planner_state.trip_details.start_date
    end_date = planner_state.trip_details.end_date
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
        # Preserve trip length if we have a return date.
        if original_return_date:
            try:
                ret_dt = date.fromisoformat(original_return_date)
                delta = ret_dt - dep_dt
                recommended_return_date = (safe_dep_dt + delta).isoformat()
            except Exception:
                recommended_return_date = original_return_date

    # Group travelers by (origin_city, destination).
    groups: Dict[Tuple[Optional[str], str], List[int]] = {}
    for idx, traveler in enumerate(travelers):
        origin_city = traveler.origin or origin_default
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
    best_price_hint: Optional[str] = None,
    best_time_hint: Optional[str] = None,
    cheap_but_long_hint: Optional[str] = None,
    recommended_option_label: Optional[str] = None,
    notes: Optional[str] = None,
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

    result = FlightSearchResult(
        task_id=task_id,
        query=query,
        summary=summary,
        best_price_hint=best_price_hint,
        best_time_hint=best_time_hint,
        cheap_but_long_hint=cheap_but_long_hint,
        recommended_option_label=recommended_option_label,
        notes=notes,
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

    save_flight_state(tool_context, flight_state)

    logger.info(
        "[Tool] apply_flight_search_results completed",
        extra={
            "num_tasks": len(flight_state.search_tasks),
            "num_results": len(flight_state.search_results),
        },
    )

    print("[Tool] apply_flight_search_results updated FlightState.overall_summary")

    return {
        "status": "success",
        "num_tasks": len(flight_state.search_tasks),
        "num_results": len(flight_state.search_results),
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
