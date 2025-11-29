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
import uuid

from src.state.planner_state import (
    PlannerState,
    TripDetails,
    Demographics,
    Preferences,
    Traveler,
)
from src.state.visa_state import VisaState
from src.state.flight_state import FlightState, FlightSearchResult, FlightOption
from src.state.flight_state import FlightState, FlightSearchTask, FlightSearchResult, FlightOption
import json

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


async def debug_parallel_planner():
    app_name = "globe-tripper-tests"
    user_id = "test-user"
    session_id = "planner_debug_session"

    # Sample planner state (similar to your live transcript)
    planner_state = PlannerState(
        trip_details=TripDetails(
            destination="LHR",
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
                Traveler(role="adult", nationality="Nigerian", origin="LOS"),
                Traveler(role="adult", nationality="Nigerian", origin="IAH"),  # Houston George Bush Intercontinental
                Traveler(role="child", nationality="American", origin="IAH"),
                Traveler(role="child", nationality="American", origin="IAH"),
            ],
        ),
        preferences=Preferences(budget_mode="economy"),
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

    # Inspect sample VisaState
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    visa_raw = (session.state or {}).get("visa") or {}
    visa_state = VisaState(**visa_raw)

    print("[STATE] VisaState after planning:")
    print(visa_state.model_dump_json(indent=2))

    # Run the end-to-end flight planning pipeline over this debug session.
    await run_flight_search_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )



if __name__ == "__main__":
    # asyncio.run(main())
    asyncio.run(debug_parallel_planner())
