import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types as genai_types

from src.tools.tools import (
    derive_accommodation_search_tasks,
    apply_accommodation_search_results,
    record_traveler_accommodation_choice,
)


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_accommodation_config = agent_configs.get("search", {})

_accommodation_instructions = (
    "You are an accommodation planning specialist for Globe Tripper.\n\n"
    "When called, you should:\n"
    "1. Use the derive_accommodation_search_tasks tool to ensure AccommodationSearchTask objects exist "
    "   for the current trip based on the planner and flight state.\n\n"
    "In your final answer, briefly describe:\n"
    "- Which destination/location you prepared tasks for.\n"
    "- The check-in and check-out dates you used (including any adjustments based on flights).\n"
    "- Any high-level accommodation planning implications that are now visible in state.\n"
)


accommodation_agent = Agent(
    name="accommodation_agent",
    model=Gemini(model=f"{_accommodation_config.get('model', '')}"),
    instruction=_accommodation_instructions,
    tools=[derive_accommodation_search_tasks],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_accommodation_config.get("temperature", 0.0)),
        max_output_tokens=int(_accommodation_config.get("max_tokens", 1000)),
    ),
)


_accommodation_apply_instructions = (
    "You help finalize accommodation planning state once search results are available.\n\n"
    "When called, you should:\n"
    "- Call apply_accommodation_search_results exactly once to update AccommodationState.overall_summary "
    "  and per-traveler accommodation choices.\n\n"
    "In your final answer, briefly confirm that you applied accommodation search results and mention "
    "how many tasks/results were processed if that information is available from the tool.\n"
)


accommodation_apply_agent = Agent(
    name="accommodation_apply_agent",
    model=Gemini(model=f"{_accommodation_config.get('model', '')}"),
    instruction=_accommodation_apply_instructions,
    tools=[apply_accommodation_search_results],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_accommodation_config.get("temperature", 0.0)),
        max_output_tokens=int(_accommodation_config.get("max_tokens", 600)),
    ),
)

_accommodation_apply_tool_instructions = (
    "You are a tool-only assistant for applying accommodation search results.\n\n"
    "When called, you must call `apply_accommodation_search_results` exactly once "
    "using the current tool context. Do not generate any additional natural-"
    "language text or summaries; the caller will inspect state and tool logs."
)


accommodation_apply_tool_agent = Agent(
    name="accommodation_apply_tool_agent",
    model=Gemini(model=f"{_accommodation_config.get('model', '')}"),
    instruction=_accommodation_apply_tool_instructions,
    tools=[apply_accommodation_search_results],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_accommodation_config.get("temperature", 0.0)),
        max_output_tokens=100,
    ),
)


_accommodation_choice_instructions = (
    "You help assign specific accommodation choices to travelers once search results exist.\n\n"
    "You will receive a short JSON payload with:\n"
    "- task_id: string (accommodation search task)\n"
    "- traveler_indexes: array of integers (indexes into PlannerState.demographics.travelers)\n"
    "- chosen_option_type: string (one of 'cheapest', 'best_location', 'family_friendly', 'balanced', 'luxury')\n"
    "- notes: optional string with brief reasoning.\n\n"
    "Your job is to call `record_traveler_accommodation_choice` EXACTLY ONCE using these fields.\n"
    "Do not call any other tools. In your final answer, briefly confirm which task_id and "
    "traveler_indexes you recorded."
)


accommodation_choice_agent = Agent(
    name="accommodation_choice_agent",
    model=Gemini(model=f"{_accommodation_config.get('model', '')}"),
    instruction=_accommodation_choice_instructions,
    tools=[record_traveler_accommodation_choice],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_accommodation_config.get("temperature", 0.0)),
        max_output_tokens=int(_accommodation_config.get("max_tokens", 400)),
    ),
)
