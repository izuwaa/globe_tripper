import asyncio
import json
import logging
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from typing import Dict, Any

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from src.agents.accommodation_agent import accommodation_agent, accommodation_apply_agent
from src.agents.accommodation_search_agent import (
    accommodation_search_tool_agent,
    accommodation_search_agent,
)
from src.agents.activity_agent import (
    activity_agent,
    activity_search_agent,
    activity_result_writer_agent,
    activity_apply_agent,
    day_itinerary_search_agent,
    activity_itinerary_agent,
)
from src.agents.dispatcher_agent import dispatcher_agent
from src.agents.flight_agent import flight_agent
from src.agents.flight_search_agent import (
    flight_search_tool_agent,
    flight_search_agent,
    flight_result_writer_agent,
)
from src.agents.parallel_planner_agent import parallel_planner_agent
from src.agents.search_agent import search_agent, visa_result_writer_agent
from src.agents.visa_agent import visa_agent
from src.agents.summary_agent import trip_summary_agent
from src.state.accommodation_state import (
    AccommodationState,
    AccommodationSearchTask,
    AccommodationSearchResult,
    AccommodationOption,
    TravelerAccommodationChoice,
)
from src.state.activity_state import ActivityState, ActivityOption, DayItineraryItem
from src.state.flight_state import FlightState, FlightSearchTask, FlightSearchResult, FlightOption
from src.state.planner_state import (
    PlannerState,
    TripDetails,
    Demographics,
    Preferences,
    Traveler,
)
from src.state.visa_state import VisaState
from src.tools.tools import _build_canonical_accommodation_options
from pydantic import BaseModel


# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
# )

load_dotenv()


class ActivitySearchAgentOutput(BaseModel):
    """
    Structured output expected from activity_search_agent when it summarizes
    google_search results for a single ActivitySearchTask.
    """

    task_id: str
    summary: str
    options: list[Dict[str, Any]] = []
    budget_hint: str | None = None
    family_friendly_hint: str | None = None
    neighborhood_hint: str | None = None
    query: str | None = None


class DaySliceItineraryOutput(BaseModel):
    """
    Structured output expected from day_itinerary_search_agent when it proposes
    itinerary items for a small slice of the trip.
    """

    items: list[Dict[str, Any]]


async def run_trip_summary(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    Generate a concise, user-facing summary of the current trip plan by
    calling the trip_summary_agent with a compact JSON payload drawn from
    planner, visa, flight, accommodation, and activity state.
    """
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    state_obj = session.state or {}

    planner_state = PlannerState(**state_obj)
    visa_raw = state_obj.get("visa") or {}
    visa_state = VisaState(**visa_raw)
    flights_raw = state_obj.get("flights") or {}
    flight_state = FlightState(**flights_raw)
    accommodation_raw = state_obj.get("accommodation") or {}
    accommodation_state = AccommodationState(**accommodation_raw)
    activities_raw = state_obj.get("activities") or {}
    activity_state = ActivityState(**activities_raw)

    # Build a richer planner payload so the summary agent can reflect nuances
    # like multiple origins and luggage without guessing.
    traveler_origins: list[Dict[str, Any]] = []
    total_luggage_count: int | None = None
    per_traveler_luggage: list[Dict[str, Any]] = []
    if planner_state.demographics.travelers:
        origins_index: Dict[str, Dict[str, Any]] = {}
        total_luggage = 0
        any_luggage = False
        for idx, t in enumerate(planner_state.demographics.travelers):
            origin = t.origin or planner_state.trip_details.origin
            key = origin or "UNKNOWN"
            if key not in origins_index:
                origins_index[key] = {
                    "origin": origin,
                    "indexes": [],
                    "roles": [],
                    "nationalities": [],
                }
            entry = origins_index[key]
            entry["indexes"].append(idx)
            entry["roles"].append(t.role)
            entry["nationalities"].append(t.nationality)

            if t.luggage_count is not None:
                any_luggage = True
                total_luggage += t.luggage_count
                per_traveler_luggage.append(
                    {
                        "index": idx,
                        "role": t.role,
                        "origin": origin,
                        "luggage_count": t.luggage_count,
                    }
                )

        traveler_origins = list(origins_index.values())
        if any_luggage:
            total_luggage_count = total_luggage

    planner_payload: Dict[str, Any] = {
        "trip_details": {
            "destination": planner_state.trip_details.destination,
            "origin": planner_state.trip_details.origin,
            "start_date": planner_state.trip_details.start_date,
            "end_date": planner_state.trip_details.end_date,
        },
        "demographics": {
            "adults": planner_state.demographics.adults,
            "children": planner_state.demographics.children,
            "seniors": planner_state.demographics.seniors,
        },
        "preferences": {
            "budget_mode": planner_state.preferences.budget_mode,
            "total_budget": planner_state.preferences.total_budget,
            "pace": planner_state.preferences.pace,
            "daily_rhythm": planner_state.preferences.daily_rhythm,
            "neighborhood_preferences": planner_state.preferences.neighborhood_preferences,
        },
        "traveler_origins": traveler_origins,
        "luggage": {
            "total_luggage_count": total_luggage_count,
            "per_traveler": per_traveler_luggage,
        },
    }

    # Build a richer visa payload with per‑traveler details and aggregated
    # sources so the summary can surface official links and health guidance.
    visa_details: list[Dict[str, Any]] = []
    for req in visa_state.requirements or []:
        visa_details.append(
            {
                "traveler_index": req.traveler_index,
                "origin": req.origin,
                "destination": req.destination,
                "nationality": req.nationality,
                "needs_visa": req.needs_visa,
                "visa_type": req.visa_type,
                "processing_time": req.processing_time,
                "cost": req.cost,
                "entry_conditions": req.entry_conditions,
                "documents_required": req.documents_required,
                "additional_notes": req.additional_notes,
            }
        )

    visa_sources: list[str] = []
    for result in visa_state.search_results or []:
        for src in result.sources or []:
            if src and src not in visa_sources:
                visa_sources.append(src)

    visa_payload: Dict[str, Any] = {
        "earliest_safe_departure_date": visa_state.earliest_safe_departure_date,
        "overall_summary": visa_state.overall_summary,
        "num_requirements": len(visa_state.requirements or []),
        "details_by_traveler": visa_details,
        "sources": visa_sources,
    }

    # Aggregate textual visa fee hints so the summary and cost blocks can
    # surface them without attempting to parse messy free‑text amounts.
    visa_fee_strings: list[str] = []
    for req in visa_state.requirements or []:
        if req.cost and (req.needs_visa is None or req.needs_visa):
            visa_fee_strings.append(req.cost)

    flight_payload: Dict[str, Any] = {
        "overall_summary": flight_state.overall_summary,
        "num_tasks": len(flight_state.search_tasks or []),
        "num_results": len(flight_state.search_results or []),
        "num_traveler_flights": len(flight_state.traveler_flights or []),
        "has_booked_flights": bool(flight_state.traveler_flights),
    }

    accommodation_payload: Dict[str, Any] = {
        "num_tasks": len(accommodation_state.search_tasks or []),
        "num_results": len(accommodation_state.search_results or []),
        "num_traveler_accommodations": len(accommodation_state.traveler_accommodations or []),
    }
    # Include a single representative chosen accommodation if present.
    chosen_accommodation = None
    if accommodation_state.traveler_accommodations:
        first_choice = accommodation_state.traveler_accommodations[0]
        if first_choice.chosen_option:
            co = first_choice.chosen_option
            chosen_accommodation = {
                "name": co.name,
                "neighborhood": co.neighborhood,
                "city": co.city,
                "country": co.country,
                "description": co.description,
                "location_label": co.location_label,
            }
    if chosen_accommodation:
        accommodation_payload["chosen"] = chosen_accommodation

    # Build a small sample of the itinerary to highlight in the summary.
    sample_days: list[Dict[str, Any]] = []
    if activity_state.day_plan:
        by_date: Dict[str, list[DayItineraryItem]] = {}
        for item in activity_state.day_plan:
            by_date.setdefault(item.date, []).append(item)
        # Expose a richer slice of the itinerary so the summary agent can
        # describe more than just the first couple of days.
        for date_str in sorted(by_date.keys())[:7]:
            items_for_day = sorted(by_date[date_str], key=lambda i: i.slot)
            sample_days.append(
                {
                    "date": date_str,
                    "items": [
                        {
                            "slot": d.slot,
                            "name": d.activity.name,
                            "neighborhood": d.activity.neighborhood,
                            "city": d.activity.city,
                        }
                        for d in items_for_day
                    ],
                }
            )

    activity_payload: Dict[str, Any] = {
        "overall_summary": activity_state.overall_summary,
        "num_search_tasks": len(activity_state.search_tasks or []),
        "num_search_results": len(activity_state.search_results or []),
        "num_day_plan_items": len(activity_state.day_plan or []),
        "sample_days": sample_days,
    }

    # Build a simple cost snapshot so the summary can comment on
    # budget vs estimated spend without doing arithmetic itself.
    cost_payload: Dict[str, Any] = {}

    # Visa costs: keep as textual notes alongside numeric flight/accommodation
    # totals so we avoid brittle parsing while still surfacing fees.
    cost_payload["visa_fee_notes"] = visa_fee_strings

    # Flight costs: aggregate per FlightSearchResult to avoid double‑counting
    # travelers that share a task.
    total_flight_low = 0.0
    total_flight_high = 0.0
    flight_currency: str | None = None
    for res in flight_state.search_results or []:
        chosen_type = res.chosen_option_type
        chosen_option = None
        for opt in res.options or []:
            if chosen_type and opt.option_type == chosen_type:
                chosen_option = opt
                break
        if chosen_option is None and res.options:
            chosen_option = res.options[0]
        if chosen_option is None:
            continue
        if chosen_option.currency and not flight_currency:
            flight_currency = chosen_option.currency
        low = chosen_option.total_price_low or chosen_option.price_per_ticket_low
        high = chosen_option.total_price_high or chosen_option.price_per_ticket_high
        if isinstance(low, (int, float)):
            total_flight_low += float(low)
        if isinstance(high, (int, float)):
            total_flight_high += float(high)

    cost_payload["total_flight_cost_low"] = total_flight_low or None
    cost_payload["total_flight_cost_high"] = total_flight_high or None
    cost_payload["flight_currency"] = flight_currency

    # Accommodation costs: aggregate per AccommodationSearchResult using the
    # chosen option when available.
    total_accom_low = 0.0
    total_accom_high = 0.0
    accom_currency: str | None = None
    for res in accommodation_state.search_results or []:
        chosen_type = res.chosen_option_type
        chosen_option = None
        for opt in res.options or []:
            if chosen_type and opt.option_type == chosen_type:
                chosen_option = opt
                break
        if chosen_option is None and res.options:
            chosen_option = res.options[0]
        if not chosen_option:
            continue
        if chosen_option.currency and not accom_currency:
            accom_currency = chosen_option.currency
        low = chosen_option.total_price_low or chosen_option.nightly_price_low
        high = chosen_option.total_price_high or chosen_option.nightly_price_high
        if isinstance(low, (int, float)):
            total_accom_low += float(low)
        if isinstance(high, (int, float)):
            total_accom_high += float(high)

    cost_payload["total_accommodation_cost_low"] = total_accom_low or None
    cost_payload["total_accommodation_cost_high"] = total_accom_high or None
    cost_payload["accommodation_currency"] = accom_currency

    # Simple combined estimate.
    if total_flight_low or total_accom_low:
        cost_payload["total_estimated_cost_low"] = (total_flight_low or 0.0) + (total_accom_low or 0.0)
        cost_payload["total_estimated_cost_high"] = (total_flight_high or 0.0) + (total_accom_high or 0.0)
    else:
        cost_payload["total_estimated_cost_low"] = None
        cost_payload["total_estimated_cost_high"] = None

    cost_payload["stated_budget"] = planner_state.preferences.total_budget

    summary_payload = {
        "planner_state": planner_payload,
        "visa_state": visa_payload,
        "flight_state": flight_payload,
        "accommodation_state": accommodation_payload,
        "activity_state": activity_payload,
        "cost_state": cost_payload,
    }

    runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=trip_summary_agent,
    )

    print("[SUMMARY] Generating trip summary...")
    final_summary_text: str | None = None
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    text=(
                        "Given the following JSON payload describing the current trip plan "
                        "(planner, visa, flight, accommodation, and activity state), write a "
                        "detailed, structured trip summary as instructed. Resolve any obvious "
                        "inconsistencies between planner dates, visa timing, and flights by "
                        "explaining them clearly to the user.\n"
                        f"{json.dumps(summary_payload)}"
                    )
                )
            ],
        ),
    ):
        if event.is_final_response and event.content and getattr(event.content, "parts", None):
            part = event.content.parts[0]
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                final_summary_text = text.strip()

    if final_summary_text:
        print("[SUMMARY] Trip summary:")
        print(final_summary_text)

        # Deterministic accommodation highlight based on the chosen option in
        # ActivityState so that a selected property is surfaced even when the
        # language model omits it.
        if accommodation_state.traveler_accommodations:
            first_choice = accommodation_state.traveler_accommodations[0]
            if first_choice.chosen_option:
                co = first_choice.chosen_option
                name = co.name or "Selected accommodation"
                city = co.city or ""
                label = co.location_label or ""
                provider = getattr(co, "provider", None)

                print("\n**Selected Accommodation (auto-detected)**")
                line = f"- {name}"
                if city:
                    line += f", {city}"
                elif label:
                    line += f" ({label})"
                print(line)
                if provider:
                    print(f"- Provider: {provider}")
                if co.description:
                    print(f"- Description: {co.description}")
                if co.url:
                    print(f"- Booking link: {co.url}")

        # Append a deterministic Budget & Costs section computed in Python so
        # that cost reasoning is accurate even when the language model is not.
        flight_low = cost_payload.get("total_flight_cost_low")
        flight_high = cost_payload.get("total_flight_cost_high")
        flight_curr = cost_payload.get("flight_currency")
        accom_low = cost_payload.get("total_accommodation_cost_low")
        accom_high = cost_payload.get("total_accommodation_cost_high")
        accom_curr = cost_payload.get("accommodation_currency")
        total_low = cost_payload.get("total_estimated_cost_low")
        total_high = cost_payload.get("total_estimated_cost_high")
        visa_fee_notes = cost_payload.get("visa_fee_notes") or []
        stated_budget = cost_payload.get("stated_budget")

        def _fmt_range(low: float | None, high: float | None, currency: str | None) -> str | None:
            if low is None and high is None:
                return None
            if low is not None and high is not None and abs(low - high) < 1e-6:
                val = f"{low:,.0f}"
            elif low is not None and high is not None:
                val = f"{low:,.0f}–{high:,.0f}"
            elif low is not None:
                val = f"{low:,.0f}+"
            else:
                val = f"{high:,.0f}"
            return f"{val} {currency or ''}".strip()

        flight_str = _fmt_range(flight_low, flight_high, flight_curr)
        accom_str = _fmt_range(accom_low, accom_high, accom_curr)
        total_str = _fmt_range(total_low, total_high, flight_curr or accom_curr)

        # Budget & Costs block temporarily disabled while cost aggregation is
        # refined. The implementation is preserved here for future re‑enablement.
        #
        # print("\n**Budget & Costs (auto-calculated)**")
        # if flight_str:
        #     print(f"- Estimated flights (covered origin groups): {flight_str}")
        # else:
        #     print("- Estimated flights: not yet available from search results.")
        #
        # if accom_str:
        #     print(f"- Estimated accommodation: {accom_str}")
        # else:
        #     print("- Estimated accommodation: not yet available from search results.")
        #
        # if total_str:
        #     print(f"- Combined estimate (flights + accommodation): {total_str}")
        #
        # if visa_fee_notes:
        #     print("- Visa processing fees (per traveler/group, textual):")
        #     for note in visa_fee_notes:
        #         print(f"  - {note}")
        #
        # if stated_budget is not None:
        #     print(f"- Stated overall budget: {stated_budget}")
        #
        # if flight_payload.get("num_results", 0) < flight_payload.get("num_tasks", 0):
        #     print(
        #         "- Note: flight costs only cover origin groups with search results; "
        #         "true total will be higher once all flights are filled in."
        #     )
        # if accommodation_payload.get("num_results", 0) < accommodation_payload.get("num_tasks", 0):
        #     print(
        #         "- Note: accommodation costs only cover destinations with search results; "
        #         "true total will be higher once all stays are included."
        #     )
                # Show only the costs we have, without claiming completeness.
        if any([flight_str, accom_str, total_str, visa_fee_notes, stated_budget is not None]):
            print("\n**Some captured costs (auto-calculated)**")

        if flight_str:
            print(f"- Flights (covered origin groups): {flight_str}")

        if accom_str:
            print(f"- Accommodation: {accom_str}")

        if total_str:
            print(f"- Combined (flights + accommodation): {total_str}")

        if visa_fee_notes:
            print("- Visa processing fees (per traveler/group, textual):")
            for note in visa_fee_notes:
                print(f"  - {note}")

        if stated_budget is not None:
            print(f"- Stated overall budget: {stated_budget}")




def _build_trip_calendar_for_itinerary(
    planner_state: PlannerState,
    flight_state: FlightState,
) -> list[Dict[str, Any]]:
    """
    Build a simple per-day calendar for itinerary planning that encodes which
    days are arrival / full / departure days and whether arrival/departure
    happens late or early.
    """
    start_str = planner_state.trip_details.start_date
    end_str = planner_state.trip_details.end_date
    if not start_str or not end_str:
        return []

    try:
        start_dt = date.fromisoformat(start_str)
        end_dt = date.fromisoformat(end_str)
    except Exception:
        return []

    arrival_dt: datetime | None = None
    departure_dt: datetime | None = None

    for choice in flight_state.traveler_flights or []:
        option = choice.chosen_option
        if option is None and choice.other_options:
            option = choice.other_options[0]
        if option is None:
            continue

        if option.outbound_arrival:
            try:
                candidate = datetime.fromisoformat(option.outbound_arrival)
            except Exception:
                candidate = None
            if candidate is not None and (arrival_dt is None or candidate < arrival_dt):
                arrival_dt = candidate

        dep_str = option.return_departure or option.return_arrival
        if dep_str:
            try:
                dep_candidate = datetime.fromisoformat(dep_str)
            except Exception:
                dep_candidate = None
            if dep_candidate is not None and (departure_dt is None or dep_candidate > departure_dt):
                departure_dt = dep_candidate

    days: list[Dict[str, Any]] = []
    current = start_dt
    while current <= end_dt:
        day_info: Dict[str, Any] = {
            "date": current.isoformat(),
            "kind": "full",
            "arrives_late": False,
            "leaves_early": False,
        }

        if arrival_dt is not None and current == arrival_dt.date():
            day_info["kind"] = "arrival"
            day_info["arrives_late"] = arrival_dt.hour >= 18

        if departure_dt is not None and current == departure_dt.date():
            if day_info["kind"] == "arrival":
                day_info["kind"] = "arrival_departure"
            else:
                day_info["kind"] = "departure"
            day_info["leaves_early"] = departure_dt.hour <= 10

        days.append(day_info)
        current = current.fromordinal(current.toordinal() + 1)

    return days

# TODO: Can we save context by remove items after certain steps?

async def main():
    # Initialize session service
    session_service = InMemorySessionService()

    app_name = "globe-tripper"
    user_id = "user-1"

    # Initialize the runner (The Engine)
    runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=dispatcher_agent,
    )

    # Create a new session with Empty PlannerState
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        state=PlannerState().model_dump(),
        session_id=session_id,
    )

    print(f"Session created with ID: {session_id}")
    print("-----")
    print("-----")

    print("Globe Tripper Here! Ready to assist you with your travel planning!")
    print("You can Type 'exit' or 'quit' to end the session.")

    print("-----")
    print("-----")

    print("Let's get started! Where would you like to go? Are you traveling alone or with others?")
    

    # Main interaction loop
    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            print("Exiting Globe Tripper. Safe travels!")
            break

        # ADK Async Run
        response_text = ""
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=user_input)],
                ),
            ):
                # Capture the final response text from the agent
                if event.is_final_response and event.content and getattr(
                    event.content, "parts", None
                ):
                    first_part = event.content.parts[0]
                    if hasattr(first_part, "text") and first_part.text:
                        response_text = first_part.text
        except ValueError as e:
            # Handle occasional model transport errors gracefully instead of crashing.
            print(f"[ERROR] Model did not return a message: {e}")
            print("Please try rephrasing or sending your message again.")
            continue

        print(f"Globe Tripper: {response_text}")

        # Debug: See the state update happening in the background
        current_session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        planner_state = PlannerState(**current_session.state or {})

        trip_details = planner_state.trip_details
        demographics = planner_state.demographics
        preferences = planner_state.preferences
        status = planner_state.status

        print(f"[DEBUG STATE]: {trip_details.model_dump()}, {demographics.model_dump()}, {preferences.model_dump()}, status={status}")

        # If intake is complete and we have not yet run visa / flight planning/search,
        # kick off those pipelines once in the background.
        if status == "planning":
            current_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            visa_raw = (current_session.state or {}).get("visa") or {}
            visa_state = VisaState(**visa_raw)

            if not visa_state.search_tasks and not visa_state.search_results:
                # Phase 1: run visa_agent to derive VisaSearchTasks.
                visa_runner = Runner(
                    session_service=session_service,
                    app_name=app_name,
                    agent=visa_agent,
                )

                print("[PLANNER] Running visa_agent to derive visa search prompts...")
                async for event in visa_runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part(
                                text=(
                                    "Inspect the current trip and travelers, and prepare visa "
                                    "search prompts for each traveler using your tools."
                                )
                            )
                        ],
                    ),
                ):
                    if event.is_final_response and event.content and getattr(event.content, "parts", None):
                        print("[PLANNER] Final reply from visa_agent:")
                        print(event.content.parts[0].text)

                # Phase 2–3: run the reusable visa search + apply pipeline.
                await run_visa_search_pipeline(
                    session_service=session_service,
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                )

            # After visa planning/search, derive and fetch flight options for this
            # session. The helper internally checks whether flight tasks/results
            # already exist so it will only run once.
            await run_flight_pipeline(
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

            # After flights are planned, derive and fetch accommodation options
            # for this session. run_accommodation_pipeline performs its own
            # checks and will only derive/search/apply once per session.
            await run_accommodation_pipeline(
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

            # After accommodation is planned, derive activities and build a
            # day-by-day itinerary for this session. run_activity_pipeline
            # performs its own checks and will only run once per session.
            await run_activity_pipeline(
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

            # Finally, generate a concise trip summary that brings together
            # visa, flights, accommodation, and itinerary in user-friendly form.
            await run_trip_summary(
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )


async def run_visa_search_pipeline(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    Run the visa search pipeline for an existing session:
    - Read VisaSearchTasks from VisaState.
    - For each pending task, call search_agent and the writer agent.
    - Ask visa_agent to apply results back into VisaRequirements.
    """
    # --- Phase 2: Run the search agent over pending VisaSearchTasks ---
    search_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=search_agent,
    )
    writer_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=visa_result_writer_agent,
    )

    # Reload visa state to find pending tasks
    session_for_search = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    visa_raw_for_search = (session_for_search.state or {}).get("visa") or {}
    visa_state_for_search = VisaState(**visa_raw_for_search)

    existing_results_by_task = {r.task_id for r in visa_state_for_search.search_results or []}
    pending_tasks = [
        t for t in visa_state_for_search.search_tasks or [] if t.task_id not in existing_results_by_task
    ]

    print(f"[SEARCH] Found {len(pending_tasks)} pending VisaSearchTask(s)")

    for idx, task in enumerate(pending_tasks, start=1):
        print(f"[SEARCH] Processing task {idx}/{len(pending_tasks)}: task_id={task.task_id}")

        search_payload = {
            "task_id": task.task_id,
            "search_prompt": task.prompt
            or (
                f"Visa requirements, documents, fees, and processing time for a "
                f"{task.nationality or 'UNKNOWN NATIONALITY'} traveler going from "
                f"{task.origin_country or 'UNKNOWN ORIGIN'} to "
                f"{task.destination_country or 'UNKNOWN DESTINATION'} "
                f"for {task.travel_purpose or 'tourism'}."
            ),
        }

        print(
            f"[SEARCH] Calling search_agent for task_id={task.task_id} "
            f"(nationality={task.nationality}, origin={task.origin_country}, "
            f"destination={task.destination_country})"
        )

        final_search_text = None
        async for event in search_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Use google_search based on the following JSON payload "
                            "and respond with a JSON object as instructed:\n"
                            f"{json.dumps(search_payload)}"
                        )
                    )
                ],
            ),
        ):
            if event.is_final_response and event.content and getattr(event.content, "parts", None):
                final_search_text = event.content.parts[0].text

        if not final_search_text:
            print(f"[SEARCH] No final response from search_agent for task_id={task.task_id}, skipping.")
            continue

        try:
            parsed = json.loads(final_search_text)
        except json.JSONDecodeError as e:
            print(
                f"[SEARCH] Failed to parse JSON for task_id={task.task_id}: {e}. "
                f"Preview: {final_search_text[:200]}..."
            )
            continue

        print(
            f"[SEARCH] Parsed result for task_id={task.task_id}: "
            f"processing_time_hint={parsed.get('processing_time_hint')!r}, "
            f"fee_hint={parsed.get('fee_hint')!r}"
        )

        writer_input = json.dumps(parsed)
        print(f"[WRITE] Calling visa_result_writer_agent for task_id={task.task_id}")

        async for event in writer_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=writer_input)],
            ),
        ):
            if event.is_final_response and event.content and getattr(event.content, "parts", None):
                print(
                    f"[WRITE] Writer agent completed for task_id={task.task_id}: "
                    f"{event.content.parts[0].text}"
                )

    # Inspect VisaState again to see search_results populated
    session_after_search = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    visa_raw_after_search = (session_after_search.state or {}).get("visa") or {}
    visa_state_after_search = VisaState(**visa_raw_after_search)

    print("[STATE] VisaState after search phase (search_results populated):")
    print(visa_state_after_search.model_dump_json(indent=2))

    # --- Phase 3: Ask visa_agent to apply search results back to VisaRequirements ---
    apply_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=visa_agent,
    )

    print("[APPLY] Running visa_agent to apply search results into visa requirements...")
    async for event in apply_runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    text=(
                        "Review the existing visa search tasks and search results, "
                        "apply them into per-traveler VisaRequirements using your tools, "
                        "and then summarize the updated visa requirements for each traveler."
                    )
                )
            ],
        ),
    ):
        if event.is_final_response and event.content and getattr(event.content, "parts", None):
            print("[APPLY] visa_agent final reply:")
            print(event.content.parts[0].text)

    # Final VisaState with requirements updated from search_results
    final_session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    final_visa_raw = (final_session.state or {}).get("visa") or {}
    final_visa_state = VisaState(**final_visa_raw)

    print("[STATE] Final VisaState after apply phase (requirements + search_results):")
    print(final_visa_state.model_dump_json(indent=2))


async def run_flight_search_pipeline(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    Run the flight search pipeline for an existing session:
    - Read FlightSearchTasks from FlightState.
    - For each pending task, call flight_search_agent and the writer agent.
    - Optionally apply results back into FlightState via flight_agent.
    """
    # Keep canonical, numeric options per task so that if the LLM fails to
    # call record_flight_search_result we can still construct
    # FlightSearchResult entries with usable price/time fields.
    canonical_flight_options_by_task: Dict[str, list[Dict[str, Any]]] = {}

    def _build_canonical_flight_options(
        task: FlightSearchTask,
        options: list[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        if not options:
            return []

        def _get_price(o: Dict[str, Any]) -> float | None:
            val = o.get("price")
            return float(val) if isinstance(val, (int, float)) else None

        def _get_duration(o: Dict[str, Any]) -> int | None:
            val = o.get("duration_minutes")
            return int(val) if isinstance(val, int) else None

        def _canonical_option(label: str, opt: Dict[str, Any]) -> Dict[str, Any]:
            legs = opt.get("legs") or []
            first_leg = legs[0] if legs else {}
            last_leg = legs[-1] if legs else {}
            dep_time = first_leg.get("departure_time")
            arr_time = last_leg.get("arrival_time")
            duration_min = _get_duration(opt)
            price = _get_price(opt)
            num_travelers = len(task.traveler_indexes or [])
            total = None
            if price is not None:
                total = price * num_travelers if num_travelers > 0 else price

            return {
                "option_type": label,
                "airlines": opt.get("airlines") or [],
                "currency": opt.get("currency") or "USD",
                "price_per_ticket_low": price,
                "price_per_ticket_high": price,
                "total_price_low": total,
                "total_price_high": total,
                "outbound_departure": dep_time,
                "outbound_arrival": arr_time,
                "return_departure": None,
                "return_arrival": None,
                "outbound_duration_hours": (duration_min / 60.0) if isinstance(duration_min, int) else None,
                "return_duration_hours": None,
                "total_outbound_duration_minutes": duration_min,
                "total_return_duration_minutes": None,
                "total_trip_duration_minutes": duration_min,
                "outbound_stops": opt.get("stops"),
                "return_stops": None,
                "notes": None,
            }

        # Cheapest by price.
        priced = [o for o in options if _get_price(o) is not None]
        cheapest = min(priced, key=_get_price) if priced else None

        # Fastest by duration.
        durd = [o for o in options if _get_duration(o) is not None]
        fastest = min(durd, key=_get_duration) if durd else None

        # Balanced: prefer "best" source, fall back to cheapest, then first option.
        balanced = next((o for o in options if o.get("source") == "best"), None)
        if balanced is None:
            balanced = cheapest or (options[0] if options else None)

        canonical: list[Dict[str, Any]] = []
        for label, opt in (("cheapest", cheapest), ("fastest", fastest), ("balanced", balanced)):
            if opt is None:
                continue
            canonical.append(_canonical_option(label, opt))

        # Deduplicate by option_type in case cheapest/fastest/balanced coincide.
        seen: Dict[str, Dict[str, Any]] = {}
        for opt in canonical:
            seen.setdefault(opt["option_type"], opt)
        return list(seen.values())
    search_tool_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=flight_search_tool_agent,
    )

    session_for_search = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    planner_state = PlannerState(**(session_for_search.state or {}))
    flights_raw = (session_for_search.state or {}).get("flights") or {}
    flight_state = FlightState(**flights_raw)

    existing_results_by_task = {r.task_id for r in flight_state.search_results or []}
    pending_tasks = [
        t for t in flight_state.search_tasks or [] if t.task_id not in existing_results_by_task
    ]

    print(f"[FLIGHT-SEARCH] Found {len(pending_tasks)} pending FlightSearchTask(s)")

    # Track which tasks we successfully reached the summarization stage for, so
    # we can add deterministic fallback results later if the model fails to
    # call record_flight_search_result.
    summary_attempted_task_ids: list[str] = []

    for task in pending_tasks:
        # Early guard: skip obviously past departure dates before calling agents/tools.
        departure_str = task.recommended_departure_date or task.original_departure_date
        if departure_str:
            try:
                dep_dt = date.fromisoformat(departure_str)
                if dep_dt < date.today():
                    print(
                        f"[FLIGHT-SEARCH] Skipping task {task.task_id}: "
                        f"departure_date {departure_str} is in the past. "
                        "Please update your trip dates to a future departure."
                    )
                    continue
            except Exception:
                # If parsing fails, fall through and let downstream logic handle it.
                pass

        search_payload = {
            "task_id": task.task_id,
            "origin": task.origin_city,
            "destination": task.destination_city,
            "departure_date": task.recommended_departure_date
            or task.original_departure_date,
            "return_date": task.recommended_return_date or task.original_return_date,
            "adults": len(task.traveler_indexes or []),
            "cabin": task.cabin_preference or "economy",
             "flexible_dates": planner_state.trip_details.flexible_dates,
            "search_prompt": task.prompt
            or (
                f"Round-trip flights from {task.origin_city or 'UNKNOWN ORIGIN'} to "
                f"{task.destination_city or 'UNKNOWN DESTINATION'} around "
                f"{task.recommended_departure_date or task.original_departure_date or 'UNKNOWN'} "
                f"with cabin preference {task.cabin_preference or 'economy'}."
            ),
        }

        # --- Stage 1: tool-only agent to call searchapi_google_flights ---
        tool_result = None
        async for event in search_tool_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Use searchapi_google_flights exactly once based on the following JSON payload, "
                            "then stop. Do not generate any natural language text; the caller will use the "
                            "tool response directly.\n"
                            f"{json.dumps(search_payload)}"
                        )
                    )
                ],
            ),
        ):
            parts = getattr(getattr(event, "content", None), "parts", None)
            if not parts:
                continue
            for part in parts:
                func_resp = getattr(part, "function_response", None)
                if func_resp and getattr(func_resp, "name", None) == "searchapi_google_flights":
                    tool_result = getattr(func_resp, "response", None)
                    break
            if tool_result is not None:
                break

        if not tool_result:
            print(
                f"[FLIGHT-SEARCH] No tool result from flight_search_tool_agent for "
                f"task_id={task.task_id}, skipping."
            )
            continue

        # --- Stage 2: LLM summarization over normalized tool_result options ---
        # Prefer options sourced from SearchAPI's `best_flights` when available.
        options_raw = (tool_result or {}).get("options") or []
        best_options = [
            o for o in options_raw if isinstance(o, dict) and o.get("source") == "best"
        ]
        candidate_options = best_options or [
            o for o in options_raw if isinstance(o, dict)
        ]

        if not candidate_options:
            print(
                f"[FLIGHT-SEARCH] Tool result for task_id={task.task_id} had no usable options, skipping."
            )
            continue

        # Capture canonicalized options so that even if the LLM fails to call
        # record_flight_search_result, we can still attach structured
        # FlightOption entries (with numeric prices) for cost calculations.
        canonical_flight_options_by_task[task.task_id] = _build_canonical_flight_options(
            task,
            candidate_options,
        )

        # From this point on we expect the LLM-backed summarization agent to
        # call record_flight_search_result for this task. If it fails, we will
        # add a lightweight fallback result so downstream logic still has a
        # FlightSearchResult to work with.
        summary_attempted_task_ids.append(task.task_id)

        from src.agents.flight_search_agent import flight_search_agent  # local import to avoid cycles
        summary_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=flight_search_agent,
        )

        summary_payload = {
            "task_id": task.task_id,
            "search_context": search_payload,
            "options": candidate_options,
        }

        async for event in summary_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Given the following JSON payload (task_id, search_context, and a list of normalized "
                            "options from searchapi_google_flights), choose and summarize the best flight options "
                            "as instructed and then call the `record_flight_search_result` tool exactly once "
                            "with your normalized findings. You may include a brief natural-language confirmation "
                            "mentioning the task_id in your final answer, but do NOT return a JSON blob.\n"
                            f"{json.dumps(summary_payload)}"
                        )
                    )
                ],
            ),
        ):
            # We rely on the record_flight_search_result tool's own logging
            # to confirm progress, so we don't print the full LLM summary here
            # to keep telemetry concise.
            continue

    # After attempting summarization for all pending tasks, ensure that each
    # of those tasks actually has a FlightSearchResult recorded. If the
    # model failed to call record_flight_search_result for any task, create
    # a minimal fallback result so downstream apply logic can still build
    # traveler_flights entries for every origin group.
    if summary_attempted_task_ids:
        session_post_summary = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        flights_raw_post = (session_post_summary.state or {}).get("flights") or {}
        flight_state_post = FlightState(**flights_raw_post)

        existing_results_by_task = {r.task_id for r in flight_state_post.search_results or []}
        missing_task_ids = [
            task_id for task_id in summary_attempted_task_ids if task_id not in existing_results_by_task
        ]

        if missing_task_ids:
            print(
                "[FLIGHT-SEARCH] No FlightSearchResult recorded after summarization for "
                f"task_id(s)={missing_task_ids}; creating stub result(s) so each group "
                "has at least a basic FlightSearchResult."
            )

            tasks_by_id = {t.task_id: t for t in (flight_state_post.search_tasks or [])}
            for task_id in missing_task_ids:
                task = tasks_by_id.get(task_id)
                if not task:
                    continue

                options_payload = canonical_flight_options_by_task.get(task_id) or []

                option_models: list[FlightOption] = []
                for opt in options_payload:
                    try:
                        option_models.append(FlightOption(**opt))
                    except Exception:
                        continue

                fallback_summary = (
                    f"Fallback summary for flights from {task.origin_city or 'UNKNOWN ORIGIN'} "
                    f"to {task.destination_city or 'UNKNOWN DESTINATION'} for travelers "
                    f"{task.traveler_indexes}: structured flight options were fetched, "
                    "but the summarization agent did not record a normalized result."
                )

                fallback_result = FlightSearchResult(
                    task_id=task_id,
                    query=task.prompt,
                    options=option_models,
                    summary=fallback_summary,
                    best_price_hint=None,
                    best_time_hint=None,
                    cheap_but_long_hint=None,
                    recommended_option_label=None,
                    notes=(
                        "Stub FlightSearchResult added by pipeline fallback; no structured "
                        "FlightOption entries are attached."
                    ),
                    chosen_option_type=None,
                    selection_reason=None,
                )
                flight_state_post.search_results.append(fallback_result)

            # Persist updated FlightState back into the session so that downstream
            # pipelines (including budget calculation and summaries) see the
            # stub FlightSearchResult entries for all origin groups. For the
            # in-memory session service used here, mutating the session object
            # is sufficient.
            state_obj = session_post_summary.state or {}
            state_obj["flights"] = flight_state_post.model_dump()
            session_post_summary.state = state_obj

    # Reload FlightState to see search_results populated
    session_after_search = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    flights_raw_after = (session_after_search.state or {}).get("flights") or {}
    flight_state_after = FlightState(**flights_raw_after)

    # Keep a snapshot of search_results immediately after the search phase so
    # we can restore them if a downstream agent accidentally clears them.
    pre_apply_search_results = list(flight_state_after.search_results or [])

    print("[STATE] FlightState after flight search phase (search_results populated):")
    print(flight_state_after.model_dump_json(indent=2))

    # Apply flight search results to derive overall_summary and per-traveler choices.
    # First, request that the LLM-backed agent calls the tool, so we preserve its
    # natural-language summary behavior for debugging.
    from src.agents.flight_agent import flight_apply_agent  # local import to avoid cycles
    apply_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=flight_apply_agent,
    )

    print("[FLIGHT-APPLY] Running flight_apply_agent to apply flight search results...")
    async for event in apply_runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    text=(
                        "Flight search results are now populated. "
                        "Call apply_flight_search_results exactly once to update the flight state summary "
                        "and per-traveler flight choices."
                    )
                )
            ],
        ),
    ):
        if event.is_final_response and event.content and getattr(event.content, "parts", None):
            print("[FLIGHT-APPLY] flight_apply_agent final reply:")
            print(event.content.parts[0].text)

    # Deterministic fallback: ensure apply_flight_search_results has actually run
    # so that FlightState.overall_summary and traveler_flights are populated even
    # if the model fails to invoke the tool.
    final_session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    final_flights_raw = (final_session.state or {}).get("flights") or {}
    final_flight_state = FlightState(**final_flights_raw)

    # If an upstream agent accidentally dropped search_results, but we have a
    # snapshot from immediately after the search phase, restore it so that
    # cost calculations and traveler_flights can still be derived.
    if not final_flight_state.search_results and pre_apply_search_results:
        print(
            "[FLIGHT-APPLY] search_results empty after flight_apply_agent; "
            "restoring snapshot captured after search phase."
        )
        final_flight_state.search_results = pre_apply_search_results
        state_after_apply = final_session.state or {}
        state_after_apply["flights"] = final_flight_state.model_dump()

    if final_flight_state.search_results and not final_flight_state.traveler_flights:
        print(
            "[FLIGHT-APPLY] traveler_flights still empty after flight_apply_agent; "
            "invoking flight_apply_tool_agent as a deterministic fallback."
        )
        from src.agents.flight_agent import flight_apply_tool_agent

        tool_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=flight_apply_tool_agent,
        )

        async for _ in tool_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text="Apply flight search results now.")],
            ),
        ):
            # The tool call updates state; no need to inspect events.
            continue

        # Reload FlightState to reflect the tool's changes.
        final_session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        final_flights_raw = (final_session.state or {}).get("flights") or {}
        final_flight_state = FlightState(**final_flights_raw)

    # Reload and print FlightState after applying results so we can inspect
    # overall_summary and traveler_flights.
    print("[STATE] FlightState after apply_flight_search_results:")
    print(final_flight_state.model_dump_json(indent=2))


async def run_flight_pipeline(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    End-to-end flight planning pipeline for an existing session:
    - Derive FlightSearchTasks using flight_agent (once per session).
    - Run the flight search pipeline to populate search_results and traveler_flights.
    """
    # Reload current flight state
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    flights_raw = (session.state or {}).get("flights") or {}
    flight_state = FlightState(**flights_raw)

    # Phase 1: derive FlightSearchTasks using visa-aware dates (only once).
    if not flight_state.search_tasks:
        flight_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=flight_agent,
        )

        print("[PLANNER] Running flight_agent to derive flight search tasks...")
        final_flight_agent_text: str | None = None
        async for event in flight_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Inspect the current trip, travelers, and visa timelines, and prepare "
                            "flight search tasks for each origin group using your tools."
                        )
                    )
                ],
            ),
        ):
            if event.is_final_response and event.content and getattr(event.content, "parts", None):
                part = event.content.parts[0]
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    final_flight_agent_text = text.strip()

        if final_flight_agent_text is not None:
            print("[PLANNER] Final reply from flight_agent:")
            print(final_flight_agent_text)

        # Reload flight state after planning so we can see derived tasks.
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        flights_raw = (session.state or {}).get("flights") or {}
        flight_state = FlightState(**flights_raw)

        print("[STATE] FlightState after planning (search_tasks derived):")
        print(flight_state.model_dump_json(indent=2))

    # Phase 2–3: run the flight search + apply pipeline once per session.
    if flight_state.search_tasks and not flight_state.search_results:
        print("[PLANNER] Running flight search pipeline...")
        await run_flight_search_pipeline(
            session_service=session_service,
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )


async def run_accommodation_pipeline(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    End-to-end accommodation planning pipeline for an existing session:
    - Derive AccommodationSearchTasks using accommodation_agent (once per session).
    - Run the accommodation search pipeline to populate search_results and traveler_accommodations.
    """
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    accommodation_raw = (session.state or {}).get("accommodation") or {}
    accommodation_state = AccommodationState(**accommodation_raw)

    # Phase 1: derive AccommodationSearchTasks (only once).
    if not accommodation_state.search_tasks:
        accom_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=accommodation_agent,
        )

        print("[PLANNER] Running accommodation_agent to derive accommodation search tasks...")
        final_accommodation_text: str | None = None
        async for event in accom_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Inspect the current trip, travelers, preferences, and flights, and prepare "
                            "accommodation search tasks for this journey using your tools."
                        )
                    )
                ],
            ),
        ):
            if event.is_final_response and event.content and getattr(event.content, "parts", None):
                part = event.content.parts[0]
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    final_accommodation_text = text.strip()

        if final_accommodation_text is not None:
            print("[PLANNER] Final reply from accommodation_agent:")
            print(final_accommodation_text)

        # Reload accommodation state after planning so we can see derived tasks.
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        accommodation_raw = (session.state or {}).get("accommodation") or {}
        accommodation_state = AccommodationState(**accommodation_raw)

        print("[STATE] AccommodationState after planning (search_tasks derived):")
        print(accommodation_state.model_dump_json(indent=2))

    # Phase 2–3: run the accommodation search + apply pipeline once per session.
    if accommodation_state.search_tasks and not accommodation_state.search_results:
        print("[PLANNER] Running accommodation search pipeline...")

        search_tool_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=accommodation_search_tool_agent,
        )

        session_for_search = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        state_dict = session_for_search.state or {}
        planner_state = PlannerState(**state_dict)
        accommodation_raw = state_dict.get("accommodation") or {}
        accommodation_state = AccommodationState(**accommodation_raw)

        existing_results_by_task = {r.task_id for r in accommodation_state.search_results or []}
        pending_tasks = [
            t for t in accommodation_state.search_tasks or [] if t.task_id not in existing_results_by_task
        ]
        summary_attempted_task_ids: list[str] = []
        canonical_options_by_task: Dict[str, list[Dict[str, Any]]] = {}

        print(f"[ACCOM-SEARCH] Found {len(pending_tasks)} pending AccommodationSearchTask(s)")

        for task in pending_tasks:
            # Build a compact search_context for this task.
            adults = sum(1 for idx in (task.traveler_indexes or []) if planner_state.demographics.travelers[idx].role == "adult") if planner_state.demographics.travelers else len(task.traveler_indexes or [])
            children = sum(1 for idx in (task.traveler_indexes or []) if planner_state.demographics.travelers[idx].role == "child") if planner_state.demographics.travelers else 0

            search_context = {
                "task_id": task.task_id,
                "location": task.location,
                "check_in_date": task.check_in_date,
                "check_out_date": task.check_out_date,
                "adults": adults,
                "children": children,
                "preferred_types": task.preferred_types,
                "room_configuration": task.room_configuration,
                "neighborhood_preferences": task.neighborhood_preferences,
                "neighborhood_avoid": task.neighborhood_avoid,
            }

            tool_result = None
            async for event in search_tool_runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            text=(
                                "Use exactly one of the accommodation search tools based on the following JSON payload, "
                                "then stop. Do not generate any natural language text; the caller will use the "
                                "tool response directly.\n"
                                f"{json.dumps(search_context)}"
                            )
                        )
                    ],
                ),
            ):
                parts = getattr(getattr(event, "content", None), "parts", None)
                if not parts:
                    continue
                for part in parts:
                    func_resp = getattr(part, "function_response", None)
                    if func_resp and getattr(func_resp, "response", None) is not None:
                        tool_result = getattr(func_resp, "response", None)
                        break
                if tool_result is not None:
                    break

            if not tool_result:
                print(
                    f"[ACCOM-SEARCH] No tool result from accommodation_search_tool_agent for "
                    f"task_id={task.task_id}, skipping."
                )
                continue

            options = (tool_result or {}).get("options") or []
            if not options:
                print(
                    f"[ACCOM-SEARCH] Tool result for task_id={task.task_id} had no usable options."
                )
                # We still want downstream logic to know that a search was attempted,
                # so record the task_id; stub results will be created later.
                summary_attempted_task_ids.append(task.task_id)
                canonical_options_by_task[task.task_id] = []
                continue
            # Filter out options that clearly cannot accommodate the traveling party
            # based on max_guests, when that metadata is available.
            num_people = adults + children if (adults or children) else len(task.traveler_indexes or [])
            if num_people and isinstance(num_people, int):
                filtered_options: list[Dict[str, Any]] = []
                for opt in options:
                    if not isinstance(opt, dict):
                        continue
                    max_guests = opt.get("max_guests")
                    if isinstance(max_guests, (int, float)) and max_guests < num_people:
                        continue
                    filtered_options.append(opt)
                if not filtered_options:
                    print(
                        f"[ACCOM-SEARCH] All options for task_id={task.task_id} "
                        f"were filtered out as under-capacity for {num_people} traveler(s)."
                    )
                    summary_attempted_task_ids.append(task.task_id)
                    canonical_options_by_task[task.task_id] = []
                    continue
                options = filtered_options
            # Build canonical options that the summarization agent + tool call will use.
            canonical_options = _build_canonical_accommodation_options(options)

            if not canonical_options:
                print(
                    f"[ACCOM-SEARCH] No canonical options could be derived for task_id={task.task_id}."
                )
                summary_attempted_task_ids.append(task.task_id)
                canonical_options_by_task[task.task_id] = []
                continue

            # --- Stage 2: LLM summarization + tool call over canonical options ---
            canonical_options_by_task[task.task_id] = canonical_options
            summary_attempted_task_ids.append(task.task_id)

            summary_runner = Runner(
                session_service=session_service,
                app_name=app_name,
                agent=accommodation_search_agent,
            )

            summary_payload = {
                "task_id": task.task_id,
                "search_context": search_context,
                "options": canonical_options,
            }

            async for _event in summary_runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            text=(
                                "Given the following JSON payload (task_id, search_context, and a list of canonical "
                                "accommodation options), choose and summarize the best options AND call "
                                "`record_accommodation_search_result` exactly once with your normalized findings. "
                                "Do not return a JSON blob yourself; rely on the tool call.\n"
                                f"{json.dumps(summary_payload)}"
                            )
                        )
                    ],
                ),
            ):
                # Tool call is the primary output; we don't need to inspect text here.
                continue

        # Persist updated AccommodationState back into the session so that
        # subsequent reads (and the apply step) see the recorded search results.
        if summary_attempted_task_ids:
            session_post_summary = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            accommodation_raw_post = (session_post_summary.state or {}).get(
                "accommodation"
            ) or {}
            accommodation_state_post = AccommodationState(**accommodation_raw_post)

            # Repair any AccommodationSearchResult entries that are missing structured
            # options by filling them from the canonical options we derived earlier.
            # This ensures downstream cost calculations and traveler_accommodations
            # selection logic have concrete AccommodationOption objects to work with,
            # even if the summarization agent omitted them from its tool call.
            if canonical_options_by_task:
                for result in accommodation_state_post.search_results or []:
                    if result.options:
                        continue
                    options_payload = canonical_options_by_task.get(result.task_id) or []
                    if not options_payload:
                        continue
                    option_models: list[AccommodationOption] = []
                    for opt in options_payload:
                        try:
                            option_models.append(AccommodationOption(**opt))
                        except Exception:
                            continue
                    if not option_models:
                        continue
                    result.options = option_models
                    # If the summarizer did not specify a chosen_option_type, default to
                    # "balanced" when available, otherwise fall back to the first option's type.
                    if not result.chosen_option_type:
                        balanced_opt = next(
                            (o for o in option_models if o.option_type == "balanced"),
                            None,
                        )
                        if balanced_opt is not None:
                            result.chosen_option_type = "balanced"
                        else:
                            result.chosen_option_type = option_models[0].option_type

            existing_results_by_task = {
                r.task_id for r in accommodation_state_post.search_results or []
            }
            # Any tasks that still lack a recorded AccommodationSearchResult (for example
            # when external APIs return errors or no options) should get a lightweight
            # fallback result so downstream agents and summaries can explain the
            # situation instead of silently omitting accommodation.
            missing_task_ids = [
                t.task_id
                for t in (accommodation_state.search_tasks or [])
                if t.task_id not in existing_results_by_task
            ]

            if missing_task_ids:
                print(
                    "[ACCOM-SEARCH] No AccommodationSearchResult recorded after summarization for "
                    f"task_id(s)={missing_task_ids}; creating stub result(s)."
                )

                tasks_by_id = {t.task_id: t for t in (accommodation_state.search_tasks or [])}
                for task_id in missing_task_ids:
                    task = tasks_by_id.get(task_id)
                    options_payload = canonical_options_by_task.get(task_id) or []

                    option_models: list[AccommodationOption] = []
                    for opt in options_payload:
                        try:
                            option_models.append(AccommodationOption(**opt))
                        except Exception:
                            continue

                    fallback_summary = (
                        f"Fallback summary for accommodation in {task.location if task else 'UNKNOWN LOCATION'} "
                        f"for travelers {task.traveler_indexes if task else 'UNKNOWN'}: "
                        "live accommodation options could not be fetched. You should still book a family‑friendly "
                        "property in a quiet, well‑connected neighbourhood that matches your room configuration "
                        "and budget."
                    )

                    best_price_hint = None
                    recommended_option_label = None
                    if options_payload:
                        cheapest = next(
                            (o for o in options_payload if o.get("option_type") == "cheapest"),
                            options_payload[0],
                        )
                        total = cheapest.get("total_price_low") or cheapest.get("total_price_high")
                        nightly = cheapest.get("nightly_price_low") or cheapest.get("nightly_price_high")
                        if total:
                            best_price_hint = f"Approximate total price for the stay: {total}"
                        elif nightly:
                            best_price_hint = f"Typical nightly rate from {nightly}"

                        balanced = next(
                            (o for o in options_payload if o.get("option_type") == "balanced"),
                            None,
                        ) or cheapest
                        if balanced.get("name"):
                            recommended_option_label = balanced["name"]

                    accommodation_state_post.search_results.append(
                        AccommodationSearchResult(
                            task_id=task_id,
                            query=task.prompt if task else None,
                            options=option_models,
                            summary=fallback_summary,
                            best_price_hint=best_price_hint,
                            best_location_hint=None,
                            family_friendly_hint=None,
                            neighborhood_hint=None,
                            recommended_option_label=recommended_option_label,
                            notes=None,
                            chosen_option_type="balanced" if options_payload else None,
                            selection_reason=(
                                "Balanced choice based on price, location, and rating."
                                if options_payload
                                else None
                            ),
                        )
                    )

            # Persist updated AccommodationState back into the session after any
            # repairs or stub creations so the apply step sees consistent,
            # option-bearing search_results.
            state_obj = session_post_summary.state or {}
            state_obj["accommodation"] = accommodation_state_post.model_dump()

        # Reload AccommodationState to see search_results populated.
        session_after_search = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        accommodation_raw_after = (session_after_search.state or {}).get("accommodation") or {}
        accommodation_state_after = AccommodationState(**accommodation_raw_after)

        print(
            "[STATE] AccommodationState after accommodation search phase (search_results populated):"
        )
        print(accommodation_state_after.model_dump_json(indent=2))

        # Apply accommodation search results to derive overall_summary and per-traveler choices.
        apply_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=accommodation_apply_agent,
        )

        print(
            "[ACCOM-APPLY] Running accommodation_apply_agent to apply accommodation search results..."
        )
        async for event in apply_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Accommodation search results are now populated. "
                            "Call apply_accommodation_search_results exactly once to update the accommodation "
                            "state summary and per-traveler accommodation choices."
                        )
                    )
                ],
            ),
        ):
            if event.is_final_response and event.content and getattr(event.content, "parts", None):
                print("[ACCOM-APPLY] accommodation_apply_agent final reply:")
                print(event.content.parts[0].text)

        # Reload AccommodationState after applying results so we can inspect it.
        final_session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        final_accommodation_raw = (final_session.state or {}).get("accommodation") or {}
        final_accommodation_state = AccommodationState(**final_accommodation_raw)

        if final_accommodation_state.search_results and not final_accommodation_state.traveler_accommodations:
            print(
                "[ACCOM-APPLY] traveler_accommodations still empty after accommodation_apply_agent; "
                "invoking accommodation_apply_tool_agent as a deterministic fallback."
            )
            from src.agents.accommodation_agent import accommodation_apply_tool_agent  # local import to avoid cycles

            apply_tool_runner = Runner(
                session_service=session_service,
                app_name=app_name,
                agent=accommodation_apply_tool_agent,
            )
            async for _ in apply_tool_runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text="Call apply_accommodation_search_results now.")],
                ),
            ):
                continue

            final_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            final_accommodation_raw = (final_session.state or {}).get("accommodation") or {}
            final_accommodation_state = AccommodationState(**final_accommodation_raw)

        # Deterministic fallback: if search_results is still empty here but we
        # have canonical options from the search step, construct minimal
        # AccommodationSearchResult and traveler_accommodations directly so
        # downstream views have something to work with.
        if not final_accommodation_state.search_results and canonical_options_by_task:
            print(
                "[ACCOM-APPLY] No AccommodationSearchResult present after apply; "
                "building fallback results from canonical options."
            )

            tasks_by_id = {t.task_id: t for t in (final_accommodation_state.search_tasks or [])}
            fallback_results: list[AccommodationSearchResult] = []

            for task_id, options_payload in canonical_options_by_task.items():
                task = tasks_by_id.get(task_id)
                if not task:
                    continue

                option_models: list[AccommodationOption] = []
                for opt in options_payload:
                    try:
                        option_models.append(AccommodationOption(**opt))
                    except Exception:
                        continue

                if not option_models:
                    continue

                fallback_summary = (
                    f"Fallback summary for accommodation in {task.location if task else 'UNKNOWN LOCATION'} "
                    f"for travelers {task.traveler_indexes if task else 'UNKNOWN'}: canonical accommodation "
                    "options were fetched, but no normalized result was recorded."
                )

                best_price_hint = None
                recommended_option_label = None
                if options_payload:
                    cheapest = next(
                        (o for o in options_payload if o.get("option_type") == "cheapest"),
                        options_payload[0],
                    )
                    total = cheapest.get("total_price_low") or cheapest.get("total_price_high")
                    nightly = cheapest.get("nightly_price_low") or cheapest.get("nightly_price_high")
                    if total:
                        best_price_hint = f"Approximate total price for the stay: {total}"
                    elif nightly:
                        best_price_hint = f"Typical nightly rate from {nightly}"

                    balanced = next(
                        (o for o in options_payload if o.get("option_type") == "balanced"),
                        None,
                    ) or cheapest
                    if balanced.get("name"):
                        recommended_option_label = balanced["name"]

                fallback_results.append(
                    AccommodationSearchResult(
                        task_id=task_id,
                        query=task.prompt if task else None,
                        options=option_models,
                        summary=fallback_summary,
                        best_price_hint=best_price_hint,
                        best_location_hint=None,
                        family_friendly_hint=None,
                        neighborhood_hint=None,
                        recommended_option_label=recommended_option_label,
                        notes=None,
                        chosen_option_type="balanced" if options_payload else None,
                        selection_reason=(
                            "Balanced choice based on price, location, and rating."
                            if options_payload
                            else None
                        ),
                    )
                )

            if fallback_results:
                final_accommodation_state.search_results = fallback_results

                # Build overall_summary and traveler_accommodations mirroring
                # apply_accommodation_search_results logic.
                lines: list[str] = []
                for result in final_accommodation_state.search_results:
                    parts: list[str] = []
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
                    final_accommodation_state.overall_summary = "\n".join(lines)

                planner_state_after = PlannerState(**(final_session.state or {}))
                travelers = planner_state_after.demographics.travelers or []
                results_by_task: Dict[str, AccommodationSearchResult] = {
                    r.task_id: r for r in final_accommodation_state.search_results or []
                }

                traveler_accommodations: list[TravelerAccommodationChoice] = []
                for traveler_index in range(len(travelers)):
                    for task in final_accommodation_state.search_tasks or []:
                        if traveler_index not in (task.traveler_indexes or []):
                            continue

                        result = results_by_task.get(task.task_id)
                        if result is None:
                            continue

                        chosen_option = None
                        other_options: list[AccommodationOption] = []

                        chosen_type = result.chosen_option_type
                        for opt in result.options or []:
                            if (
                                chosen_type
                                and opt.option_type == chosen_type
                                and chosen_option is None
                            ):
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

                final_accommodation_state.traveler_accommodations = traveler_accommodations

                state_obj_final = final_session.state or {}
                state_obj_final["accommodation"] = final_accommodation_state.model_dump()

        print("[STATE] AccommodationState after apply_accommodation_search_results:")
        print(final_accommodation_state.model_dump_json(indent=2))


async def run_activity_pipeline(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    End-to-end activity and itinerary planning pipeline for an existing session:
    - Derive ActivitySearchTasks using activity_agent (once per session).
    - Run the activity search pipeline to populate ActivityState.search_results.
    - Apply activity search results to build a coarse day-by-day itinerary.
    """
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    activities_raw = (session.state or {}).get("activities") or {}
    activity_state = ActivityState(**activities_raw)

    # Phase 1: derive ActivitySearchTasks (only once).
    if not activity_state.search_tasks:
        act_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=activity_agent,
        )

        print("[PLANNER] Running activity_agent to derive activity search tasks...")
        final_activity_text: str | None = None
        async for event in act_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Inspect the current trip details, interests, and preferences, and prepare "
                            "activity search tasks for this journey using your tools."
                        )
                    )
                ],
            ),
        ):
            if event.is_final_response and event.content and getattr(event.content, "parts", None):
                part = event.content.parts[0]
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    final_activity_text = text.strip()

        if final_activity_text is not None:
            print("[PLANNER] Final reply from activity_agent:")
            print(final_activity_text)

        # Reload activity state after planning so we can see derived tasks.
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        activities_raw = (session.state or {}).get("activities") or {}
        activity_state = ActivityState(**activities_raw)

        print("[STATE] ActivityState after planning (search_tasks derived):")
        print(activity_state.model_dump_json(indent=2))

    # Phase 2: run the activity search pipeline once per session.
    if activity_state.search_tasks and not activity_state.search_results:
        print("[PLANNER] Running activity search pipeline...")

        search_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=activity_search_agent,
        )
        writer_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=activity_result_writer_agent,
        )

        session_for_search = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        state_dict = session_for_search.state or {}
        activities_raw = state_dict.get("activities") or {}
        activity_state = ActivityState(**activities_raw)

        existing_results_by_task = {r.task_id for r in activity_state.search_results or []}
        pending_tasks = [
            t for t in activity_state.search_tasks or [] if t.task_id not in existing_results_by_task
        ]

        print(f"[ACTIVITY-SEARCH] Found {len(pending_tasks)} pending ActivitySearchTask(s)")

        for task in pending_tasks:
            search_context = task.model_dump()

            # Phase 1: use google_search via activity_search_agent to build a JSON result.
            search_payload = {
                "task_id": task.task_id,
                "search_context": search_context,
            }

            final_search_text: str | None = None
            async for event in search_runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            text=(
                                "Given the following JSON payload (task_id and search_context), use google_search "
                                "to discover suitable activities and respond with a SINGLE JSON object as instructed.\n"
                                f"{json.dumps(search_payload)}"
                            )
                        )
                    ],
                ),
            ):
                if event.is_final_response and event.content and getattr(
                    event.content, "parts", None
                ):
                    # Some responses include function_call parts; scan all parts
                    # for the first non-empty text segment.
                    for part in event.content.parts:
                        text = getattr(part, "text", None)
                        if isinstance(text, str) and text.strip():
                            final_search_text = text.strip()
                            break

            if not final_search_text:
                print(
                    f"[ACTIVITY-SEARCH] No final response from activity_search_agent for "
                    f"task_id={task.task_id}, skipping."
                )
                continue

            # Some model responses may include JSON inside Markdown code fences.
            # Strip any leading/trailing ``` blocks before attempting to parse.
            cleaned_search_text = final_search_text.strip()
            if cleaned_search_text.startswith("```"):
                first_nl = cleaned_search_text.find("\n")
                if first_nl != -1:
                    cleaned_search_text = cleaned_search_text[first_nl + 1 :]
                if cleaned_search_text.rstrip().endswith("```"):
                    cleaned_search_text = cleaned_search_text.rstrip()[:-3]
                cleaned_search_text = cleaned_search_text.strip()

            try:
                parsed = ActivitySearchAgentOutput.model_validate_json(cleaned_search_text)
            except Exception as e:
                # Fallback: some responses are a single-element JSON array.
                # If so, treat the first item as the payload.
                parsed = None
                try:
                    raw = json.loads(cleaned_search_text)
                    if isinstance(raw, list) and raw:
                        parsed = ActivitySearchAgentOutput.model_validate(raw[0])
                except Exception:
                    parsed = None

                if parsed is None:
                    print(
                        f"[ACTIVITY-SEARCH] Failed to parse JSON into ActivitySearchAgentOutput "
                        f"for task_id={task.task_id}: {e}. "
                        f"Preview: {cleaned_search_text[:1000]}..."
                    )
                    continue

            writer_input = parsed.model_dump_json()
            async for event in writer_runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=writer_input)],
                ),
            ):
                if event.is_final_response and event.content and getattr(event.content, "parts", None):
                    print(
                        f"[ACTIVITY-SEARCH] Writer agent completed for task_id={task.task_id}"
                    )

        # Reload ActivityState after search so we can see recorded results.
        session_after_search = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        activities_raw_after = (session_after_search.state or {}).get("activities") or {}
        activity_state_after = ActivityState(**activities_raw_after)

        print(
            "[STATE] ActivityState after activity search phase: "
            f"num_tasks={len(activity_state_after.search_tasks)}, "
            f"num_results={len(activity_state_after.search_results)}"
        )

        # Build an itinerary using a chunked, LLM-native itinerary pipeline. We plan
        # a few days at a time so that prompts stay compact: one agent uses
        # google_search to propose items, and a second agent writes those items
        # into ActivityState via record_day_itinerary.
        combined_session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        combined_state = combined_session.state or {}
        planner_state = PlannerState(**combined_state)
        flights_raw_for_itinerary = combined_state.get("flights") or {}
        accommodation_raw_for_itinerary = combined_state.get("accommodation") or {}
        activities_raw_for_itinerary = combined_state.get("activities") or {}

        flight_state = FlightState(**flights_raw_for_itinerary)
        accommodation_state = AccommodationState(**accommodation_raw_for_itinerary)
        activity_state_for_itinerary = ActivityState(**activities_raw_for_itinerary)

        # Build a simple per-day calendar, using flight arrival/departure times
        # where available to tag arrival/departure days.
        trip_calendar = _build_trip_calendar_for_itinerary(planner_state, flight_state)
        if not trip_calendar:
            print("[ACTIVITY-ITINERARY] Trip calendar could not be derived; skipping itinerary planning.")
        else:
            # Derive a base neighborhood hint from chosen accommodation, if available.
            base_neighborhood = None
            if accommodation_state.traveler_accommodations:
                first_choice = accommodation_state.traveler_accommodations[0]
                if first_choice.chosen_option and first_choice.chosen_option.neighborhood:
                    base_neighborhood = first_choice.chosen_option.neighborhood

            travelers = planner_state.demographics.travelers or []
            travelers_payload = [
                {
                    "index": idx,
                    "role": t.role,
                    "age": t.age,
                    "nationality": t.nationality,
                }
                for idx, t in enumerate(travelers)
            ]

            preferences_payload = {
                "daily_rhythm": planner_state.preferences.daily_rhythm,
                "pace": planner_state.preferences.pace,
                "budget_mode": planner_state.preferences.budget_mode,
            }

            # Collapse ActivitySearchResult options into a small list of suggestions
            # that the itinerary agent can treat as anchors.
            activity_suggestions: list[Dict[str, Any]] = []
            for result in activity_state_for_itinerary.search_results or []:
                for idx, opt in enumerate(result.options or []):
                    activity_suggestions.append(
                        {
                            "source_task_id": result.task_id,
                            "option_index": idx,
                            "name": opt.name,
                            "neighborhood": opt.neighborhood,
                            "city": opt.city,
                            "url": opt.url,
                            "notes": opt.notes,
                        }
                    )

            # Plan the trip in small chunks to keep the prompt size manageable.
            chunk_size = 3
            day_search_runner = Runner(
                session_service=session_service,
                app_name=app_name,
                agent=day_itinerary_search_agent,
            )

            # Accumulate all DayItineraryItem entries locally; we will write them
            # back into ActivityState in one shot at the end. Track which major
            # activities have already been scheduled so we avoid repeating the
            # same attraction across multiple days.
            accumulated_itinerary_items: list[DayItineraryItem] = list(activity_state_for_itinerary.day_plan or [])
            seen_keys: Dict[str, set[str]] = {
                "by_url": set(),
                "by_name_city": set(),
            }
            # Seed the seen sets with anything that may already exist.
            for existing in accumulated_itinerary_items:
                url = existing.activity.url
                name = (existing.activity.name or "").strip().lower()
                city = (existing.activity.city or "").strip().lower()
                if url:
                    seen_keys["by_url"].add(url)
                if name:
                    seen_keys["by_name_city"].add(f"{name}::{city}")

            # Track how many distinct neighborhoods we visit per day so we keep
            # travel reasonable (avoid bouncing across many far-flung areas).
            neighborhoods_by_date: Dict[str, set[str]] = {}

            for i in range(0, len(trip_calendar), chunk_size):
                chunk = trip_calendar[i : i + chunk_size]
                day_itinerary_payload = {
                    "days": chunk,
                    "base_city": planner_state.trip_details.destination,
                    "base_neighborhood": base_neighborhood,
                    "travelers": travelers_payload,
                    "preferences": preferences_payload,
                    "activity_suggestions": activity_suggestions,
                }

                print(
                    "[ACTIVITY-ITINERARY] Running activity_itinerary_agent to plan "
                    f"{len(chunk)} day(s) starting {chunk[0]['date']}..."
                )

                # Phase 1: use day_itinerary_search_agent (with google_search) to propose
                # concrete itinerary items for this slice.
                final_day_text: str | None = None
                async for event in day_search_runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part(
                                text=(
                                    "Given the following JSON payload describing a small slice of the trip "
                                    "(days, base_city, base_neighborhood, travelers, preferences, and "
                                    "activity_suggestions), use google_search as needed and respond with a "
                                    "SINGLE JSON object of the form {\"items\": [...]} as instructed.\n"
                                    f"{json.dumps(day_itinerary_payload)}"
                                )
                            )
                        ],
                    ),
                ):
                    if event.is_final_response and event.content and getattr(event.content, "parts", None):
                        for part in event.content.parts:
                            text = getattr(part, "text", None)
                            if isinstance(text, str) and text.strip():
                                final_day_text = text.strip()
                                break

                if not final_day_text:
                    print(
                        "[ACTIVITY-ITINERARY] No final response from day_itinerary_search_agent "
                        f"for days starting {chunk[0]['date']}; skipping this slice."
                    )
                    continue

                # Some model responses may include JSON inside Markdown code fences.
                # Strip any leading/trailing ``` blocks before attempting to parse.
                cleaned_day_text = final_day_text.strip()
                if cleaned_day_text.startswith("```"):
                    # Drop the first fence line.
                    first_nl = cleaned_day_text.find("\n")
                    if first_nl != -1:
                        cleaned_day_text = cleaned_day_text[first_nl + 1 :]
                    # Drop a trailing fence if present.
                    if cleaned_day_text.rstrip().endswith("```"):
                        cleaned_day_text = cleaned_day_text.rstrip()[:-3]
                    cleaned_day_text = cleaned_day_text.strip()

                try:
                    parsed_day = DaySliceItineraryOutput.model_validate_json(cleaned_day_text)
                except Exception as e:
                    # Fallback: some responses return a raw list of items
                    # instead of an object of the form {"items": [...]}. If
                    # so, wrap the list into the expected shape.
                    parsed_day = None
                    try:
                        raw_payload = json.loads(cleaned_day_text)
                        if isinstance(raw_payload, list):
                            parsed_day = DaySliceItineraryOutput(items=raw_payload)
                    except Exception:
                        parsed_day = None

                    if parsed_day is None:
                        print(
                            "[ACTIVITY-ITINERARY] Failed to parse JSON from day_itinerary_search_agent "
                            f"for days starting {chunk[0]['date']}: {e}. "
                            f"Preview: {cleaned_day_text[:1000]}..."
                        )
                        continue

                print(
                    "[ACTIVITY-ITINERARY] day_itinerary_search_agent produced "
                    f"{len(parsed_day.items)} item(s) for days starting {chunk[0]['date']}"
                )
                # Phase 2: deterministically turn the JSON items into DayItineraryItem
                # entries, applying simple deduping and neighborhood caps, then append
                # them to our accumulated itinerary.
                for raw in parsed_day.items or []:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        date_str = raw.get("date")
                        slot_raw = raw.get("slot")
                        name = raw.get("name") or raw.get("title") or raw.get("label")

                        if not isinstance(date_str, str) or not isinstance(slot_raw, str):
                            continue
                        if not isinstance(name, str) or not name.strip():
                            continue

                        slot_normalized = slot_raw.strip().lower()
                        if slot_normalized not in ("morning", "afternoon", "evening"):
                            continue

                        task_id = raw.get("task_id") or "*"

                        traveler_indexes_raw = raw.get("traveler_indexes")
                        if traveler_indexes_raw:
                            traveler_indexes = list(traveler_indexes_raw)
                        else:
                            traveler_indexes = list(range(len(travelers)))

                        # Deduping: skip exact repeats of the same major attraction
                        # across the whole trip. We treat items with a URL or a
                        # non-generic name as candidates for deduping and allow
                        # generic meal labels like "Hotel breakfast" on multiple days.
                        name_norm = name.strip().lower()
                        city_norm = (raw.get("city") or "").strip().lower()
                        url = raw.get("url")

                        is_meal = any(
                            token in name_norm
                            for token in ("breakfast", "lunch", "dinner")
                        )

                        if url:
                            if url in seen_keys["by_url"]:
                                continue
                        elif not is_meal:
                            key = f"{name_norm}::{city_norm}"
                            if key in seen_keys["by_name_city"]:
                                continue

                        # Simple neighborhood cap per day: avoid visiting more
                        # than two distinct neighborhoods on the same date so
                        # the day feels geographically coherent.
                        neighborhood = (raw.get("neighborhood") or "").strip()
                        if neighborhood:
                            used_neighborhoods = neighborhoods_by_date.setdefault(date_str, set())
                            if neighborhood not in used_neighborhoods and len(used_neighborhoods) >= 2:
                                continue

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
                            traveler_indexes=traveler_indexes,
                            task_id=task_id,
                            activity=activity_model,
                            notes=raw.get("notes"),
                        )
                        if url:
                            seen_keys["by_url"].add(url)
                        elif not is_meal:
                            seen_keys["by_name_city"].add(f"{name_norm}::{city_norm}")
                        if neighborhood:
                            neighborhoods_by_date.setdefault(date_str, set()).add(neighborhood)
                        accumulated_itinerary_items.append(item)
                    except Exception:
                        # Skip malformed items; others will still be recorded.
                        continue

        # Persist the accumulated itinerary back into ActivityState for this session.
        final_session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        state_obj = final_session.state or {}
        final_activities_raw = state_obj.get("activities") or {}
        final_activity_state = ActivityState(**final_activities_raw)
        final_activity_state.day_plan = accumulated_itinerary_items

        if accumulated_itinerary_items:
            lines: list[str] = []
            for item in accumulated_itinerary_items:
                lines.append(f"{item.date} {item.slot}: {item.activity.name}")
            final_activity_state.overall_summary = "\n".join(lines)

        state_obj["activities"] = final_activity_state.model_dump()

        print(
            "[STATE] ActivityState after itinerary planning: "
            f"num_day_plan_items={len(final_activity_state.day_plan)}"
        )
        if final_activity_state.day_plan:
            print("[STATE] Sample itinerary for first few days:")
            by_date: Dict[str, list[DayItineraryItem]] = {}
            for item in final_activity_state.day_plan:
                by_date.setdefault(item.date, []).append(item)
            for date_str in sorted(by_date.keys())[:3]:
                print(f"  {date_str}:")
                for item in sorted(by_date[date_str], key=lambda i: i.slot):
                    print(f"    {item.slot}: {item.activity.name}")


async def debug_parallel_planner():
    app_name = "globe-tripper-tests"
    user_id = "test-user"
    session_id = "planner_debug_session"

    # Sample planner state (similar to your live transcript)
    planner_state = PlannerState(
        trip_details=TripDetails(
            destination="London, UK",
            origin="LOS",  # Lagos Murtala Muhammed International Airport
            origin_airport_code="LOS",
            destination_airport_code="LHR",
            start_date="2025-12-01",
            end_date="2025-12-20",
        ),
        demographics=Demographics(
            adults=2,
            children=2,
            seniors=0,
            nationality=["Nigerian", "American"],
            travelers=[
                Traveler(role="adult", age=35, nationality="Nigerian", origin="LOS"),
                Traveler(role="adult", age=34, nationality="Nigerian", origin="IAH"),  # Houston George Bush Intercontinental
                Traveler(role="child", age=3, nationality="American", origin="IAH"),
                Traveler(role="child", age=5, nationality="American", origin="IAH"),
            ],
        ),
        preferences=Preferences(
            budget_mode="standard",
            accommodation_preferences=["family-friendly hotel", "4-star rating", "breakfast included"],
            room_configuration="2 adults and 2 young children in one family room or two connecting rooms",
            neighborhood_preferences=["safe", "central", "near parks"],
            neighborhood_avoid=["party areas", "noisy nightlife"],
            transport_preferences=["public transport", "ride-hailing"],
            daily_rhythm="Kids nap 1–3pm; prefer early evenings for activities.",
        ),
        status="planning",
    )

    session_service = InMemorySessionService()
    # For this debug path, we only pre-populate the core PlannerState and then
    # run the normal visa, flight, accommodation, activity, and summary
    # pipelines so you can exercise the full end-to-end flow without the
    # interactive intake loop.
    base_state = planner_state.model_dump()

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=base_state,
    )

    # Phase 1: derive VisaSearchTasks using visa_agent so that the reusable
    # visa search pipeline can find concrete tasks to execute, mirroring the
    # interactive planner flow.
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    visa_raw = (session.state or {}).get("visa") or {}
    visa_state = VisaState(**visa_raw)

    if not visa_state.search_tasks and not visa_state.search_results:
        visa_runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=visa_agent,
        )

        print("[PLANNER] Running visa_agent to derive visa search prompts...")
        async for event in visa_runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=(
                            "Inspect the current trip and travelers, and prepare visa "
                            "search prompts for each traveler using your tools."
                        )
                    )
                ],
            ),
        ):
            if event.is_final_response and event.content and getattr(
                event.content, "parts", None
            ):
                print("[PLANNER] Final reply from visa_agent:")
                print(event.content.parts[0].text)

    # Run the full planner pipelines for this sample session.
    await run_visa_search_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    await run_flight_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    await run_accommodation_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    await run_activity_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    await run_trip_summary(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )



if __name__ == "__main__":
    # Default entrypoint: interactive Globe Tripper experience that uses the
    # same visa/flight/accommodation/activity/summary pipelines as the
    # debug_parallel_planner above.
    asyncio.run(main())

    # For local debugging of the end-to-end planner without the interactive
    # intake loop, you can temporarily comment out the line above and
    # uncomment the following:
    # asyncio.run(debug_parallel_planner())
