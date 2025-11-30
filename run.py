import asyncio
from typing import Dict, Any
from datetime import date
from dotenv import load_dotenv
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types as genai_types
from src.agents.dispatcher_agent import dispatcher_agent
from src.agents.parallel_planner_agent import parallel_planner_agent
from src.agents.search_agent import search_agent, visa_result_writer_agent
from src.agents.visa_agent import visa_agent
from src.agents.flight_agent import flight_agent
from src.agents.flight_search_agent import (
    flight_search_tool_agent,
    flight_search_agent,
    flight_result_writer_agent,
)
from src.agents.accommodation_agent import accommodation_agent, accommodation_apply_agent
from src.agents.accommodation_search_agent import (
    accommodation_search_tool_agent,
    accommodation_search_agent,
)
import uuid

from src.state.planner_state import (
    PlannerState,
    TripDetails,
    Demographics,
    Preferences,
    Traveler,
)
from src.state.visa_state import VisaState
from src.state.flight_state import FlightState, FlightSearchTask, FlightSearchResult, FlightOption
from src.state.accommodation_state import (
    AccommodationState,
    AccommodationSearchResult,
    AccommodationOption,
    TravelerAccommodationChoice,
)
from src.tools.tools import _build_canonical_accommodation_options
import json
from types import SimpleNamespace

load_dotenv()

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

                fallback_summary = (
                    f"Fallback summary for flights from {task.origin_city or 'UNKNOWN ORIGIN'} "
                    f"to {task.destination_city or 'UNKNOWN DESTINATION'} for travelers "
                    f"{task.traveler_indexes}: structured flight options were fetched, "
                    "but the summarization agent did not record a normalized result."
                )

                fallback_result = FlightSearchResult(
                    task_id=task_id,
                    query=task.prompt,
                    options=[],
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

            # Persist updated FlightState back into the session. InMemorySessionService
            # keeps state on the session object, so mutating session.state is
            # sufficient for this debug/runtime pipeline.
            state_obj = session_post_summary.state or {}
            state_obj["flights"] = flight_state_post.model_dump()

    # Reload FlightState to see search_results populated
    session_after_search = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    flights_raw_after = (session_after_search.state or {}).get("flights") or {}
    flight_state_after = FlightState(**flights_raw_after)

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
                    f"[ACCOM-SEARCH] Tool result for task_id={task.task_id} had no usable options, skipping."
                )
                continue
            # Build canonical options that the summarization agent + tool call will use.
            canonical_options = _build_canonical_accommodation_options(options)

            if not canonical_options:
                print(
                    f"[ACCOM-SEARCH] No canonical options could be derived for task_id={task.task_id}, skipping."
                )
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
            accommodation_raw_post = (session_post_summary.state or {}).get("accommodation") or {}
            accommodation_state_post = AccommodationState(**accommodation_raw_post)

            existing_results_by_task = {r.task_id for r in accommodation_state_post.search_results or []}
            missing_task_ids = [
                task_id for task_id in summary_attempted_task_ids if task_id not in existing_results_by_task
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
                        f"for travelers {task.traveler_indexes if task else 'UNKNOWN'}: canonical accommodation "
                        "options were fetched, but no result was recorded."
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

                # Persist updated AccommodationState back into the session.
                # InMemorySessionService keeps state on the session object, so
                # mutating session_post_summary.state is sufficient (mirrors
                # the flight pipeline behavior above).
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
            accommodation_preferences=["family-friendly hotel", "4-star hotel"],
            room_configuration="2 adults and 2 young children in one family room or two connecting rooms",
            neighborhood_preferences=["safe", "central", "near parks"],
            neighborhood_avoid=["party areas", "noisy nightlife"],
            transport_preferences=["public transport", "ride-hailing"],
            daily_rhythm="Kids nap 1–3pm; prefer early evenings for activities.",
        ),
        status="planning",
    )

    session_service = InMemorySessionService()

    # Pre-populate session state with PlannerState and a sample VisaState that
    # already contains a visa-aware earliest_safe_departure_date so we can
    # focus on flight planning without re-running the visa search pipeline.
    base_state = planner_state.model_dump()
    sample_visa_state = VisaState(
        requirements=[],
        overall_summary=None,
        search_tasks=[],
        search_results=[],
        earliest_safe_departure_date="2025-12-05",
    )
    base_state["visa"] = sample_visa_state.model_dump()


    sample_flights = FlightState(
        search_tasks=[
            FlightSearchTask(
                task_id="debug_LOS_LHR_0",
                traveler_indexes=[0],
                origin_city="LOS",
                destination_city="LHR",
                original_departure_date="2025-12-01",
                original_return_date="2025-12-20",
                recommended_departure_date="2025-12-05",
                recommended_return_date="2025-12-20",
                cabin_preference="economy",
                budget_mode="economy",
                purpose="flight_options_lookup",
            ),
            FlightSearchTask(
                task_id="debug_IAH_LHR_1",
                traveler_indexes=[1, 2, 3],
                origin_city="IAH",
                destination_city="LHR",
                original_departure_date="2025-12-01",
                original_return_date="2025-12-20",
                recommended_departure_date="2025-12-05",
                recommended_return_date="2025-12-20",
                cabin_preference="economy",
                budget_mode="economy",
                purpose="flight_options_lookup",
            ),
        ],
        search_results=[
            FlightSearchResult(
                task_id="debug_LOS_LHR_0",
                query="Debug LOS-LHR sample",
                options=[
                    FlightOption(option_type="cheapest", airlines=["SampleAir LOS-LHR"], currency="USD"),
                    FlightOption(option_type="fastest", airlines=["FastAir LOS-LHR"], currency="USD"),
                ],
                summary="Sample LOS-LHR options for debugging.",
                best_price_hint="~$1000–$1200 per ticket",
                best_time_hint="Fastest option is non-stop with FastAir.",
                cheap_but_long_hint="Cheapest option has one stop and longer duration.",
                recommended_option_label="FastAir LOS-LHR (fastest)",
                notes="Debug sample only; not from live API.",
                chosen_option_type="balanced",
                selection_reason="Balanced option trades off price and time.",
            ),
            FlightSearchResult(
                task_id="debug_IAH_LHR_1",
                query="Debug IAH-LHR sample",
                options=[
                    FlightOption(option_type="cheapest", airlines=["SampleAir IAH-LHR"], currency="USD"),
                    FlightOption(option_type="fastest", airlines=["FastAir IAH-LHR"], currency="USD"),
                ],
                summary="Sample IAH-LHR options for debugging.",
                best_price_hint="~$900–$1100 per ticket",
                best_time_hint="Fastest option is non-stop with FastAir.",
                cheap_but_long_hint="Cheapest option has one stop and longer duration.",
                recommended_option_label="SampleAir / FastAir IAH-LHR (balanced)",
                notes="Debug sample only; not from live API.",
                chosen_option_type="balanced",
                selection_reason="Balanced option trades off price and time.",
            ),
        ],
    )
    base_state["flights"] = sample_flights.model_dump()


    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=base_state,
    )

    # Inspect sample VisaState and FlightState
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    visa_raw = (session.state or {}).get("visa") or {}
    visa_state = VisaState(**visa_raw)

    print("[STATE] VisaState after planning:")
    print(visa_state.model_dump_json(indent=2))

    flights_raw = (session.state or {}).get("flights") or {}
    flight_state = FlightState(**flights_raw)

    print(
        "[STATE] FlightState with pre-populated search_tasks and "
        "search_results (debug only, no flight API calls):"
    )
    print(flight_state.model_dump_json(indent=2))

    # Run the accommodation pipeline on this debug session so we can
    # exercise end-to-end visa → flights → accommodation behavior without
    # calling external flight APIs (flights are pre-populated above).
    await run_accommodation_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )



if __name__ == "__main__":
    # asyncio.run(main())
    # To run the debug planner instead, comment out the line above and
    # uncomment the following:
    asyncio.run(debug_parallel_planner())
