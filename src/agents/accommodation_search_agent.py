import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types as genai_types

from src.tools.tools import (
    searchapi_airbnb_properties,
    searchapi_google_hotels_properties,
    record_accommodation_search_result,
)


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_search_config = agent_configs.get("search", {})

_accommodation_tool_instructions = (
    "You are a tool-only assistant for accommodation planning.\n\n"
    "You will receive ONE accommodation search task at a time in the user message as a JSON payload.\n"
    "The payload will include at least:\n"
    "  - task_id: string\n"
    "  - search_context: object with fields like:\n"
    "      - location: city or area string (e.g. 'London', 'Shoreditch')\n"
    "      - check_in_date, check_out_date (ISO dates)\n"
    "      - adults, children\n"
    "      - preferred_types: array of strings like ['hotel'], ['vacation_rental'], or both\n"
    "      - accommodation_preferences / room_configuration / notes (free-form text)\n\n"
    "Your only job is to choose the most appropriate search engine based on these preferences and then call "
    "exactly ONE of the following tools:\n"
    "  - `searchapi_google_hotels_properties` for hotel-style stays (hotels, resorts, standard rooms)\n"
    "  - `searchapi_airbnb_properties` for vacation rentals / apartments / private homes / penthouses\n\n"
    "Guidance:\n"
    "- If preferred_types or free-text preferences clearly emphasize hotels, resorts, or similar, prefer "
    "  `searchapi_google_hotels_properties`.\n"
    "- If they emphasize apartments, homes, villas, Airbnbs, penthouses, or other private stays, prefer "
    "  `searchapi_airbnb_properties`.\n"
    "- If both are mentioned, use your judgment to pick whichever best matches the dominant intent for this task.\n\n"
    "Important:\n"
    "- You MUST call exactly ONE of these tools per task.\n"
    "- After calling the tool, do not generate any additional natural-language text or summaries. "
    "The caller will use the tool response directly.\n"
)


accommodation_search_tool_agent = Agent(
    name="accommodation_search_tool_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_accommodation_tool_instructions,
    tools=[searchapi_airbnb_properties, searchapi_google_hotels_properties],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 800)),
    ),
)


_accommodation_search_instructions = (
    "You are a focused research assistant for accommodation planning AND state writing.\n\n"
    "You will receive a single JSON payload describing an accommodation search task and a list of canonical options.\n"
    "The payload has the following structure:\n"
    "  - task_id: string\n"
    "  - search_context: object (location, dates, guests, preferences, etc.)\n"
    "  - options: array of normalized accommodation option objects derived from the Airbnb or Google Hotels tools.\n\n"
    "Your job is to read the `options` list (and the search_context) and then call the "
    "`record_accommodation_search_result` tool EXACTLY ONCE with your normalized findings:\n"
    "- Provide a concise summary of the most suitable options, noting type, neighborhood/location, and price hints.\n"
    "- Pass through the canonical options array (cheapest, best_location, family_friendly, balanced, or luxury) "
    "with fields already normalized in the payload.\n"
    "- Fill the hint fields when possible: best_price_hint, best_location_hint, family_friendly_hint, neighborhood_hint.\n"
    "- Choose a recommended_option_label when one option clearly stands out.\n"
    "- Set chosen_option_type to the canonical option type you recommend "
    "('cheapest', 'best_location', 'family_friendly', 'balanced', or 'luxury') and include a short selection_reason.\n\n"
    "When calling `record_accommodation_search_result`, you MUST provide:\n"
    "- task_id: echo the task_id from the payload.\n"
    "- summary: concise natural-language summary (1–3 sentences).\n"
    "- options: the canonical options array you were given (you may drop low-quality items if needed).\n"
    "- best_price_hint / best_location_hint / family_friendly_hint / neighborhood_hint: strings or null.\n"
    "- recommended_option_label: string or null.\n"
    "- notes: string or null.\n"
    "- chosen_option_type: one of ['cheapest', 'best_location', 'family_friendly', 'balanced', 'luxury'] or null.\n"
    "- selection_reason: short string or null.\n\n"
    "Important:\n"
    "- Do NOT return a JSON blob in your text response.\n"
    "- Your primary output should be the `record_accommodation_search_result` tool call; "
    "you may include a very short confirmation mentioning the task_id.\n"
)


accommodation_search_agent = Agent(
    name="accommodation_search_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_accommodation_search_instructions,
    tools=[record_accommodation_search_result],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 1000)),
    ),
)


_accommodation_writer_instructions = (
    "You help persist accommodation search results into the accommodation state.\n\n"
    "When the user provides a JSON object describing an accommodation search result, you MUST call the\n"
    "`record_accommodation_search_result` tool with the corresponding fields:\n"
    "- task_id (string)\n"
    "- summary (string)\n"
    "- options (array of option objects, may be empty)\n"
    "- best_price_hint (string or null)\n"
    "- best_location_hint (string or null)\n"
    "- family_friendly_hint (string or null)\n"
    "- neighborhood_hint (string or null)\n"
    "- recommended_option_label (string or null)\n"
    "- notes (string or null)\n"
    "- chosen_option_type (string or null, one of 'cheapest', 'best_location', 'family_friendly', 'balanced', 'luxury')\n"
    "- selection_reason (string or null)\n\n"
    "Do not call any other tools. In your final answer, briefly confirm the task_id you recorded."
)


accommodation_result_writer_agent = Agent(
    name="accommodation_result_writer_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_accommodation_writer_instructions,
    tools=[record_accommodation_search_result],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 500)),
    ),
)
