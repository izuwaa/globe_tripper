import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types as genai_types

from src.tools.tools import record_flight_search_result, searchapi_google_flights


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_search_config = agent_configs.get("search", {})

_flight_tool_instructions = (
    "You are a tool-only assistant for flight planning.\n\n"
    "You will receive ONE flight search task at a time in the user message as a JSON payload. "
    "Your only job is to call the `searchapi_google_flights` tool with parameters derived from that JSON "
    "(origin/destination airport codes, departure/return dates, passengers, cabin).\n\n"
    "Important:\n"
    "- Always call `searchapi_google_flights` exactly once per task.\n"
    "- After calling the tool, do not generate any additional natural-language text or summaries. "
    "The caller will use the tool response directly.\n"
)


flight_search_tool_agent = Agent(
    name="flight_search_tool_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_flight_tool_instructions,
    tools=[searchapi_google_flights],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 800)),
    ),
)


_flight_search_instructions = (
    "You are a focused research assistant for flight planning.\n\n"
    "You will receive a single JSON payload describing a flight search task and a list of normalized options.\n"
    "The payload has the following structure:\n"
    "  - task_id: string\n"
    "  - search_context: object (origin, destination, dates, passengers, cabin, etc.)\n"
    "  - options: array of normalized option objects derived from the `searchapi_google_flights` tool.\n\n"
    "Your job is to read the `options` list (and the search_context) and then call the "
    "`record_flight_search_result` tool EXACTLY ONCE with your normalized findings:\n"
    "- Choose up to three canonical options: cheapest, fastest, and balanced.\n"
    "- For each chosen option, fill the option object fields expected by the tool.\n"
    "- Also provide overall summary and hint fields.\n\n"
    "When calling `record_flight_search_result`, you MUST provide:\n"
    "- task_id: echo the task_id you were given.\n"
    "- summary: concise natural-language summary of typical routes, durations, and airlines. "
    "  Clearly describe the cheapest reasonable option, the fastest reasonable option, and a balanced option.\n"
    "- options: array of up to 3 option objects, each with:\n"
    "    - option_type: one of ['cheapest', 'fastest', 'balanced']\n"
    "    - airlines: array of short airline names (e.g. ['British Airways'])\n"
    "    - currency: string like 'USD' or 'GBP'\n"
    "    - price_per_ticket_low: number or null (lower bound per ticket)\n"
    "    - price_per_ticket_high: number or null (upper bound per ticket)\n"
    "    - total_price_low: number or null (lower bound total for all travelers)\n"
    "    - total_price_high: number or null (upper bound total for all travelers)\n"
    "    - outbound_departure: ISO 8601 datetime string or null\n"
    "    - outbound_arrival: ISO 8601 datetime string or null\n"
    "    - return_departure: ISO 8601 datetime string or null\n"
    "    - return_arrival: ISO 8601 datetime string or null\n"
    "    - outbound_duration_hours: number or null\n"
    "    - return_duration_hours: number or null\n"
    "    - outbound_stops: integer or null\n"
    "    - return_stops: integer or null\n"
    "    - notes: string or null (very short notes like baggage caveats)\n"
    "- best_price_hint: typical lowest reasonable price range per traveler (string or null).\n"
    "- best_time_hint: typical fastest or most time-efficient option (duration and stops) (string or null).\n"
    "- cheap_but_long_hint: description of the cheapest but significantly longer options (string or null).\n"
    "- recommended_option_label: short label describing your recommended balanced option (string or null).\n"
    "- notes: additional caveats (string or null).\n"
    "- chosen_option_type: which canonical option type you ultimately recommend "
    "  ('cheapest', 'fastest', or 'balanced').\n"
    "- selection_reason: short explanation of why you chose that option type.\n\n"
    "Important:\n"
    "- Do NOT return a JSON blob in your text response.\n"
    "- Your primary output should be the `record_flight_search_result` tool call; "
    "you may include a very short natural-language confirmation mentioning the task_id.\n"
)


flight_search_agent = Agent(
    name="flight_search_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_flight_search_instructions,
    tools=[record_flight_search_result],
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
    "- options (array of option objects, may be empty)\n"
    "- best_price_hint (string or null)\n"
    "- best_time_hint (string or null)\n"
    "- cheap_but_long_hint (string or null)\n"
    "- recommended_option_label (string or null)\n"
    "- notes (string or null)\n"
    "- chosen_option_type (string or null, one of 'cheapest', 'fastest', 'balanced')\n"
    "- selection_reason (string or null)\n\n"
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
