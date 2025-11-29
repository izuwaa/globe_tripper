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
    "   when available.\n\n"
    "In your final answer, briefly describe:\n"
    "- Which origin→destination groups you prepared tasks for.\n"
    "- Any adjustments you made to departure dates due to visa processing timelines.\n"
    "- Any high-level flight planning implications that are now visible in state.\n"
)


flight_agent = Agent(
    name="flight_agent",
    model=Gemini(model=f"{_flight_config.get('model', '')}"),
    instruction=_flight_instructions,
    tools=[derive_flight_search_tasks],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_flight_config.get("temperature", 0.0)),
        max_output_tokens=int(_flight_config.get("max_tokens", 1000)),
    ),
)


_flight_apply_instructions = (
    "You help finalize flight planning state once search results are available.\n\n"
    "When called, you should:\n"
    "- Call apply_flight_search_results exactly once to update FlightState.overall_summary "
    "  and per-traveler flight choices.\n"
    "- Do NOT call derive_flight_search_tasks.\n\n"
    "In your final answer, briefly confirm that you applied flight search results and mention "
    "how many tasks/results were processed if that information is available from the tool.\n"
)


flight_apply_agent = Agent(
    name="flight_apply_agent",
    model=Gemini(model=f"{_flight_config.get('model', '')}"),
    instruction=_flight_apply_instructions,
    tools=[apply_flight_search_results],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_flight_config.get("temperature", 0.0)),
        max_output_tokens=int(_flight_config.get("max_tokens", 600)),
    ),
)


_flight_apply_tool_instructions = (
    "You are a tool-only assistant for applying flight search results.\n\n"
    "When called, you must call `apply_flight_search_results` exactly once "
    "using the current tool context. Do not generate any additional natural-"
    "language text or summaries; the caller will inspect state and tool logs."
)


flight_apply_tool_agent = Agent(
    name="flight_apply_tool_agent",
    model=Gemini(model=f"{_flight_config.get('model', '')}"),
    instruction=_flight_apply_tool_instructions,
    tools=[apply_flight_search_results],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_flight_config.get("temperature", 0.0)),
        max_output_tokens=100,
    ),
)
