import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types as genai_types

from src.tools.tools import derive_flight_search_tasks, apply_flight_search_results


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_flight_config = agent_configs.get("flight", {})

_flight_instructions = (
    "You are a flight planning specialist for Globe Tripper.\n\n"
    "When called, you should:\n"
    "1. Use the derive_flight_search_tasks tool to ensure FlightSearchTask objects exist "
    "   for each relevant origin→destination group, using visa-aware recommended dates "
    "   when available.\n"
    "2. If FlightState.search_results already contains results, call apply_flight_search_results "
    "   to update FlightState.overall_summary.\n\n"
    "In your final answer, briefly describe:\n"
    "- Which origin→destination groups you prepared tasks for.\n"
    "- Any adjustments you made to departure dates due to visa processing timelines.\n"
    "- Any high-level flight planning implications that are now visible in state.\n"
)


flight_agent = Agent(
    name="flight_agent",
    model=Gemini(model=f"{_flight_config.get('model', '')}"),
    instruction=_flight_instructions,
    tools=[derive_flight_search_tasks, apply_flight_search_results],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_flight_config.get("temperature", 0.0)),
        max_output_tokens=int(_flight_config.get("max_tokens", 1000)),
    ),
)

