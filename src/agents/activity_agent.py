import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import google_search
from google.genai import types as genai_types

from src.tools.tools import (
    derive_activity_search_tasks,
    record_activity_search_result,
    apply_activity_search_results,
    record_day_itinerary,
)


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_activity_config = agent_configs.get("search", {})

_activity_planner_instructions = (
    "You are an activity and itinerary planning specialist for Globe Tripper.\n\n"
    "When called, you should:\n"
    "- Use the derive_activity_search_tasks tool to ensure ActivitySearchTask objects exist "
    "  for the current trip based on the planner state (destination, dates, interests).\n\n"
    "In your final answer, briefly describe:\n"
    "- Which destination you prepared tasks for.\n"
    "- The date range you used.\n"
    "- Any high-level activity planning themes that are now visible in state.\n"
)


activity_agent = Agent(
    name="activity_agent",
    model=Gemini(model=f"{_activity_config.get('model', '')}"),
    instruction=_activity_planner_instructions,
    tools=[derive_activity_search_tasks],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_activity_config.get("temperature", 0.0)),
        max_output_tokens=int(_activity_config.get("max_tokens", 2500)),
    ),
)


_activity_search_instructions = (
    "You are a focused research assistant for activities and itinerary suggestions.\n\n"
    "You will receive a single JSON payload describing an ActivitySearchTask and its search context.\n"
    "The payload has the following structure:\n"
    "  - task_id: string\n"
    "  - search_context: object (destination, dates, interests, must_do, nice_to_have, etc.)\n\n"
    "Your job is to:\n"
    "1. Use the google_search tool with one or more well-constructed queries based on the search_context "
    "   to find activities, attractions, and experiences that match the travelers' interests and constraints.\n"
    "2. Read and synthesize the results into a SINGLE JSON object with the following keys. The JSON MUST be "
    "   strictly valid and self-contained (no trailing text, no extra JSON objects) and MUST appear as one "
    "   compact line (no pretty-printing or extra newlines):\n"
    '   - \"task_id\": string (echo the task_id you were given)\n'
    '   - \"summary\": string (concise natural-language summary of the types of activities discovered)\n'
    '   - \"options\": array of at most 3 objects, each approximating an ActivityOption with keys like '
    '\"name\", \"category\", \"location_label\", \"neighborhood\", \"city\", \"country\", '
    '\"price_per_person_low\", \"price_per_person_high\", \"currency\", '
    '\"suitable_for_children\", \"url\", \"notes\" (you may omit fields you cannot infer). '
    'Keep \"notes\" to a single short sentence for each option.\n'
    '   - \"budget_hint\": string or null (typical price level for the suggested activities)\n'
    '   - \"family_friendly_hint\": string or null (how suitable the set is for families / children)\n'
    '   - \"neighborhood_hint\": string or null (neighborhoods or areas that stand out)\n'
    '   - \"query\": string (the main google_search query you used)\n\n'
    "Keep the summary reasonably short. Respond with JSON ONLY, no additional commentary or markdown. "
    "Do NOT include code fences, explanations, or multiple JSON objects.\n"
)


activity_search_agent = Agent(
    name="activity_search_agent",
    model=Gemini(model=f"{_activity_config.get('model', '')}"),
    instruction=_activity_search_instructions,
    tools=[google_search],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_activity_config.get("temperature", 0.0)),
    ),
)


_activity_writer_instructions = (
    "You help persist activity search results into the activity state.\n\n"
    "When the user provides a JSON object describing an activity search result, you MUST call the\n"
    "`record_activity_search_result` tool with the corresponding fields:\n"
    "- task_id (string)\n"
    "- summary (string)\n"
    "- options (array of option objects, may be empty)\n"
    "- budget_hint (string or null)\n"
    "- family_friendly_hint (string or null)\n"
    "- neighborhood_hint (string or null)\n"
    "- query (string or null)\n\n"
    "Do not call any other tools. In your final answer, briefly confirm the task_id you recorded."
)


activity_result_writer_agent = Agent(
    name="activity_result_writer_agent",
    model=Gemini(model=f"{_activity_config.get('model', '')}"),
    instruction=_activity_writer_instructions,
    tools=[record_activity_search_result],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_activity_config.get("temperature", 0.0)),
        max_output_tokens=int(_activity_config.get("max_tokens", 2500)),
    ),
)


_activity_apply_instructions = (
    "You help finalize the activity planning state once search results are available.\n\n"
    "When called, you should:\n"
    "- Call apply_activity_search_results exactly once to update ActivityState.day_plan and overall_summary.\n\n"
    "In your final answer, briefly confirm that you applied activity search results and mention "
    "how many itinerary items were produced if that information is available from the tool.\n"
)


activity_apply_agent = Agent(
    name="activity_apply_agent",
    model=Gemini(model=f"{_activity_config.get('model', '')}"),
    instruction=_activity_apply_instructions,
    tools=[apply_activity_search_results],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_activity_config.get("temperature", 0.0)),
        max_output_tokens=int(_activity_config.get("max_tokens", 2500)),
    ),
)


_day_itinerary_search_instructions = (
    "You are a day-by-day itinerary planner for families on vacation.\n\n"
    "You will receive a single JSON payload describing a short range of dates plus context. The payload has the keys:\n"
    "  - days: array of objects, each with:\n"
    "      - date: ISO date string\n"
    "      - kind: 'arrival', 'full', 'departure', or 'arrival_departure'\n"
    "      - arrives_late: boolean (true if arrival is late in the evening)\n"
    "      - leaves_early: boolean (true if departure is early in the morning)\n"
    "  - base_city: string (main city for the stay)\n"
    "  - base_neighborhood: string or null (typical base neighborhood, e.g. 'Kensington')\n"
    "  - travelers: array of objects with at least:\n"
    "      - index: integer traveler index\n"
    "      - role: 'adult' | 'child' | 'senior'\n"
    "      - age: integer or null\n"
    "  - preferences: object with keys like daily_rhythm, pace, budget_mode\n"
    "  - activity_suggestions: array of simple activity suggestions (name, neighborhood, city, url, notes, source_task_id, option_index)\n\n"
    "ASSUMPTIONS (think like a human on vacation):\n"
    "- Most activities take a few hours including travel (2–4h), not full days.\n"
    "- Families generally leave from the base_neighborhood and return there to sleep.\n"
    "- It is fine and healthy to have some light or rest days.\n"
    "- Meals matter: plan where breakfast, lunch, and dinner fit into the day.\n\n"
    "COMMON-SENSE PLANNING GUIDELINES:\n"
    "- Arrival days: keep very light (maybe one gentle nearby activity) and simple meals near the accommodation.\n"
    "- Departure days: treat as low-activity; short, nearby activities only, and plenty of time to get to the airport.\n"
    "- Full days: aim for at most 1–2 major activities, plus breakfast, lunch, and dinner.\n"
    "- Respect daily_rhythm: honour nap windows (like 1–3pm) and avoid late, exhausting evenings with young kids.\n"
    "- Use base_neighborhood and activity_suggestions to cluster stops so travel time stays reasonable; avoid jumping between far-apart neighborhoods in one day.\n"
    "- Budget_mode hints:\n"
    "    - 'economy': default to cheaper/free activities and assume breakfast at the hotel if typical; lunches can be quick, dinners modest.\n"
    "    - 'standard' or 'luxury': allow some paid attractions and restaurant meals, but still keep pacing realistic for a family.\n"
    "- It is allowed to mark a day as a 'rest day' (e.g. only meals and maybe a short park visit), especially in longer trips.\n\n"
    "YOUR JOB:\n"
    "1. Use base_city, base_neighborhood, travelers, preferences, and activity_suggestions to construct a realistic plan just for the given days.\n"
    "2. When helpful, use the google_search tool to look up concrete activities, parks, or family-friendly restaurants that fit the context.\n"
    "3. Return a SINGLE JSON object with the following structure. The JSON MUST be strictly valid and self-contained "
    "(no trailing text, no extra JSON objects) and MUST appear as one compact line (no pretty-printing or extra newlines):\n"
    '   - \"items\": array of objects, each with:\n'
    '       - \"date\": ISO date string (one of the dates in the days array)\n'
    '       - \"slot\": \"morning\" | \"afternoon\" | \"evening\"\n'
    '       - \"name\": short display name of the activity OR meal (e.g. \"Science Museum\", \"Hotel breakfast\", \"Dinner near South Kensington\")\n'
    '       - \"notes\": short note if useful (e.g. \"arrive 15 min early\", \"book tickets in advance\")\n'
    '       - \"task_id\": optional ActivitySearchTask.task_id this item came from (use \"*\" if unclear)\n'
    '       - \"neighborhood\": optional neighborhood string\n'
    '       - \"city\": optional city string\n'
    '       - \"url\": optional URL string\n'
    '       - \"traveler_indexes\": optional array of traveler indexes; if omitted, assume all travelers\n\n'
    "When planning, you MUST keep the overall day realistic for a vacationing family: limited activities, time to travel to/from places, and space for rest.\n"
    "Respond with JSON ONLY as described above. Do NOT include code fences, explanations, or multiple JSON objects."
)


day_itinerary_search_agent = Agent(
    name="day_itinerary_search_agent",
    model=Gemini(model=f"{_activity_config.get('model', '')}"),
    instruction=_day_itinerary_search_instructions,
    tools=[google_search],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_activity_config.get("temperature", 0.0)),
        max_output_tokens=int(_activity_config.get("max_tokens", 2500)),
    ),
)


_activity_itinerary_instructions = (
    "You help persist day-by-day itineraries into the activity state.\n\n"
    "When the user provides a JSON object describing a small set of itinerary items, you MUST call the\n"
    "`record_day_itinerary` tool with the corresponding fields:\n"
    "- items: array of objects, each with:\n"
    "    - date: ISO date string\n"
    "    - slot: 'morning', 'afternoon', or 'evening'\n"
    "    - name: short display name of the activity or meal\n"
    "    - notes: optional short string with any important details\n"
    "    - task_id: optional ActivitySearchTask.task_id this item came from (use '*' if unclear)\n"
    "    - traveler_indexes: optional array of traveler indexes; if omitted, assume all travelers\n"
    "    - neighborhood, city, url: optional context fields if present\n"
    "- overall_summary: short natural-language summary of the itinerary you constructed for these items.\n\n"
    "IMPORTANT:\n"
    "- You MUST satisfy your task by calling the record_day_itinerary tool EXACTLY ONCE: never zero times, never more than once.\n"
    "- Do NOT call any other tools.\n"
    "- Do NOT attempt to return the itinerary as JSON or markdown in your text.\n"
    "- Do NOT describe the full itinerary in free-form text; the canonical output must be the tool call.\n"
    "- If the JSON is missing some optional fields (e.g. neighborhood, url), you may omit them in the tool call.\n"
    "In your final answer, briefly confirm the number of items you recorded."
)


activity_itinerary_agent = Agent(
    name="activity_itinerary_agent",
    model=Gemini(model=f"{_activity_config.get('model', '')}"),
    instruction=_activity_itinerary_instructions,
    tools=[record_day_itinerary],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_activity_config.get("temperature", 0.0)),
        max_output_tokens=int(_activity_config.get("max_tokens", 2500)),
    ),
)
