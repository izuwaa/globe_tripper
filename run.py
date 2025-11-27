import asyncio
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
from src.state.flight_state import FlightState
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
    search_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=flight_search_agent,
    )
    writer_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=flight_result_writer_agent,
    )

    session_for_search = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    flights_raw = (session_for_search.state or {}).get("flights") or {}
    flight_state = FlightState(**flights_raw)

    existing_results_by_task = {r.task_id for r in flight_state.search_results or []}
    pending_tasks = [
        t for t in flight_state.search_tasks or [] if t.task_id not in existing_results_by_task
    ]

    print(f"[FLIGHT-SEARCH] Found {len(pending_tasks)} pending FlightSearchTask(s)")

    for idx, task in enumerate(pending_tasks, start=1):
        print(
            f"[FLIGHT-SEARCH] Processing task {idx}/{len(pending_tasks)}: "
            f"task_id={task.task_id}"
        )

        search_payload = {
            "task_id": task.task_id,
            "search_prompt": task.prompt
            or (
                f"Round-trip flights from {task.origin_city or 'UNKNOWN ORIGIN'} to "
                f"{task.destination_city or 'UNKNOWN DESTINATION'} around "
                f"{task.recommended_departure_date or task.original_departure_date or 'UNKNOWN'} "
                f"with cabin preference {task.cabin_preference or 'economy'}."
            ),
        }

        print(
            f"[FLIGHT-SEARCH] Calling flight_search_agent for task_id={task.task_id} "
            f"(origin={task.origin_city}, destination={task.destination_city}, "
            f"recommended_departure={task.recommended_departure_date})"
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
            print(
                f"[FLIGHT-SEARCH] No final response from flight_search_agent for "
                f"task_id={task.task_id}, skipping."
            )
            continue

        try:
            parsed = json.loads(final_search_text)
        except json.JSONDecodeError as e:
            print(
                f"[FLIGHT-SEARCH] Failed to parse JSON for task_id={task.task_id}: {e}. "
                f"Preview: {final_search_text[:200]}..."
            )
            continue

        print(
            f"[FLIGHT-SEARCH] Parsed result for task_id={task.task_id}: "
            f"best_price_hint={parsed.get('best_price_hint')!r}, "
            f"best_time_hint={parsed.get('best_time_hint')!r}"
        )

        writer_input = json.dumps(parsed)
        print(f"[FLIGHT-WRITE] Calling flight_result_writer_agent for task_id={task.task_id}")

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
                    f"[FLIGHT-WRITE] Writer agent completed for task_id={task.task_id}: "
                    f"{event.content.parts[0].text}"
                )

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


async def debug_parallel_planner():
    app_name = "globe-tripper-tests"
    user_id = "test-user"
    session_id = "planner_debug_session"

    # Sample planner state (similar to your live transcript)
    planner_state = PlannerState(
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
            nationality=["Nigerian", "American"],
            travelers=[
                Traveler(role="adult", nationality="Nigerian", origin="Nigeria"),
                Traveler(role="adult", nationality="Nigerian", origin="Houston, Texas"),
                Traveler(role="child", nationality="American", origin="Houston, Texas"),
                Traveler(role="child", nationality="American", origin="Houston, Texas"),
            ],
        ),
        preferences=Preferences(budget_mode="luxury"),
        status="planning",
    )

    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=planner_state.model_dump(),
    )

    # --- Phase 1: Run visa_agent directly to build VisaSearchTasks ---
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

    # Inspect VisaState written by visa/planner agents
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    visa_raw = (session.state or {}).get("visa") or {}
    visa_state = VisaState(**visa_raw)

    print("[STATE] VisaState after planning:")
    print(visa_state.model_dump_json(indent=2))

    # Run the reusable visa search + apply pipeline
    await run_visa_search_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    # --- Phase 4: Run flight_agent to derive FlightSearchTasks using visa-aware dates ---
    flight_runner = Runner(
        session_service=session_service,
        app_name=app_name,
        agent=flight_agent,
    )

    print("[PLANNER] Running flight_agent to derive flight search tasks...")
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
            print("[PLANNER] Final reply from flight_agent:")
            print(event.content.parts[0].text)

    # Inspect FlightState written by flight_agent
    session_after_flight_planning = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    flights_raw = (session_after_flight_planning.state or {}).get("flights") or {}
    flight_state = FlightState(**flights_raw)

    print("[STATE] FlightState after planning (search_tasks derived):")
    print(flight_state.model_dump_json(indent=2))

    # --- Phase 5: Run the flight search pipeline over pending FlightSearchTasks ---
    await run_flight_search_pipeline(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )


if __name__ == "__main__":
    asyncio.run(debug_parallel_planner())


# if __name__ == "__main__":
#     asyncio.run(main())
