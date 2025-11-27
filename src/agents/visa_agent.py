import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types as genai_types

from src.tools.tools import build_visa_search_prompt, apply_visa_search_results
from src.state.state_utils import get_planner_state, get_visa_state


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")
visa_instructions_path = os.path.join(
    os.path.dirname(__file__), "../artifacts/visa/instruction.md"
)


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)

with open(visa_instructions_path, "r") as f:
    _visa_instructions = f.read()

_visa_config = agent_configs.get("visa", {})


def _visa_state_reader(tool_context):
    """
    Lightweight tool that exposes just the information needed for visa
    planning in a simple, unambiguous structure.

    It returns:
      - destination: overall trip destination (country / city).
      - start_date, end_date: trip dates if available.
      - travelers: a list of travelers with index, role, nationality, origin.
    """
    planner_state = get_planner_state(tool_context)

    destination = planner_state.trip_details.destination
    start_date = planner_state.trip_details.start_date
    end_date = planner_state.trip_details.end_date

    travelers = []
    for idx, traveler in enumerate(planner_state.demographics.travelers or []):
        travelers.append(
            {
                "index": idx,
                "role": traveler.role,
                "nationality": traveler.nationality,
                "origin": traveler.origin or planner_state.trip_details.origin,
            }
        )

    return {
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
        "travelers": travelers,
    }


visa_agent = Agent(
    name="visa_agent",
    model=Gemini(model=f"{_visa_config.get('model', '')}"),
    instruction=_visa_instructions,
    tools=[
        _visa_state_reader,
        build_visa_search_prompt,
        apply_visa_search_results,
    ],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_visa_config.get("temperature", 0.2)),
        max_output_tokens=int(_visa_config.get("max_tokens", 1000)),
    ),
)
