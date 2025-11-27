import json
import os
from pathlib import Path

import pytest
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types as genai_types

from src.agents.dispatcher_agent import dispatcher_agent
from src.state.planner_state import PlannerState


SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("ENABLE_LLM_INTAKE_TESTS"),
    reason="Requires live LLM and valid credentials.",
)
async def test_intake_scenarios():
    app_name = "globe-tripper-tests"
    user_id = "test-user"

    scenarios = json.loads(SCENARIOS_PATH.read_text())

    for case in scenarios:
        session_service = InMemorySessionService()
        session_id = f"test_{case['id']}"

        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state=PlannerState().model_dump(),
        )

        runner = Runner(
            session_service=session_service,
            app_name=app_name,
            agent=dispatcher_agent,
        )

        async for _ in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=case["input"])],
            ),
        ):
            pass

        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        final_state = PlannerState(**(session.state or {}))
        expected = case["expected_state"]

        if "preferences" in expected:
            exp_pref = expected["preferences"]
            if "budget_mode" in exp_pref:
                assert final_state.preferences.budget_mode == exp_pref["budget_mode"], case["id"]

        if "trip_details" in expected:
            exp_td = expected["trip_details"]
            if "destination" in exp_td:
                actual_dest = (final_state.trip_details.destination or "").lower()
                expected_dest = exp_td["destination"].lower()
                assert expected_dest in actual_dest, case["id"]


        if "demographics" in expected:
            exp_demo = expected["demographics"]
            if "adults" in exp_demo:
                assert final_state.demographics.adults == exp_demo["adults"], case["id"]
            if "children" in exp_demo:
                assert final_state.demographics.children == exp_demo["children"], case["id"]
