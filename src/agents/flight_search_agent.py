import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import google_search
from google.genai import types as genai_types

from src.tools.tools import record_flight_search_result


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_search_config = agent_configs.get("search", {})

_flight_search_instructions = (
    "You are a focused research assistant for flight planning.\n\n"
    "You will receive ONE flight search task at a time in the user message. "
    "Each task includes a task_id and a natural-language search_prompt describing what flights to look up.\n\n"
    "Your job is to:\n"
    "1. Use the google_search tool with a clear query derived from the provided search_prompt.\n"
    "2. Read and synthesize the results, focusing on typical options from reputable flight search engines, "
    "   airline sites, or travel aggregators.\n"
    "3. Return your findings as a SINGLE JSON object with the following keys. "
    "The JSON MUST be strictly valid and self-contained (no trailing text, no extra JSON objects):\n"
    '   - \"task_id\": string (echo the task_id you were given)\n'
    '   - \"summary\": string (concise natural-language summary of typical routes, durations, and airlines). '
    'Clearly describe the cheapest reasonable option, the fastest reasonable option, and a balanced option.\n'
    '   - \"best_price_hint\": string or null (typical lowest reasonable price range per traveler)\n'
    '   - \"best_time_hint\": string or null (typical fastest or most time-efficient option: duration and stops)\n'
    '   - \"cheap_but_long_hint\": string or null (description of the cheapest but significantly longer options)\n'
    '   - \"recommended_option_label\": string or null (short label describing your recommended balanced option)\n'
    '   - \"notes\": string or null (additional caveats: seasonal pricing, baggage notes, etc.)\n\n'
    "Keep the summary reasonably short (a few sentences). "
    "Respond with JSON ONLY, no additional commentary or markdown."
)


flight_search_agent = Agent(
    name="flight_search_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_flight_search_instructions,
    tools=[google_search],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 1000)),
    ),
)


_flight_writer_instructions = (
    "You help persist flight search results into the flight state.\n\n"
    "When the user provides a JSON object describing a flight search result, you MUST call the\n"
    "`record_flight_search_result` tool with the corresponding fields:\n"
    "- task_id (string)\n"
    "- summary (string)\n"
    "- best_price_hint (string or null)\n"
    "- best_time_hint (string or null)\n"
    "- cheap_but_long_hint (string or null)\n"
    "- recommended_option_label (string or null)\n"
    "- notes (string or null)\n\n"
    "Do not call any other tools. In your final answer, briefly confirm the task_id you recorded."
)


flight_result_writer_agent = Agent(
    name="flight_result_writer_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_flight_writer_instructions,
    tools=[record_flight_search_result],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 500)),
    ),
)

