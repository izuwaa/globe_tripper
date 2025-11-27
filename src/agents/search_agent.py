import os

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import google_search
from google.genai import types as genai_types

from src.tools.tools import record_visa_search_result


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)


_search_config = agent_configs.get("search", {})

_search_instructions = (
    "You are a focused research assistant for visa planning.\n\n"
    "You will receive ONE visa search task at a time in the user message. "
    "Each task includes a task_id, traveler context, and a natural-language search_prompt "
    "describing what to look up.\n\n"
    "Your job is to:\n"
    "1. Use the google_search tool with a clear query derived from the provided search_prompt.\n"
    "2. Read and synthesize the results, focusing ONLY on official government and approved visa "
    "application centre websites.\n"
    "3. Return your findings as a SINGLE JSON object with the following keys. "
    "The JSON MUST be strictly valid and self-contained (no trailing text, no extra JSON objects):\n"
    '   - "task_id": string (echo the task_id you were given)\n'
    '   - "summary": string (concise natural-language summary of visa requirements, documents, fees, and timelines). '
    'In this summary, explicitly state whether a visa is required using phrases like '
    '"Visa required: yes" or "Visa required: no", and where applicable clearly name the primary visa type '
    '(e.g. "Visa type: Standard Visitor Visa").\n'
    '   - "processing_time_hint": string or null (typical processing time)\n'
    '   - "fee_hint": string or null (typical fee or fee range)\n'
    '   - "notes": string or null (any important caveats)\n'
    '   - "sources": array of strings (2 to 3 short source labels such as "UK government visa guidance" or '
    '"Official TLS visa centre site"; do NOT include long redirect URLs or raw tool URLs)\n\n'
    "Keep the summary reasonably short (a few sentences). "
    "Respond with JSON ONLY, no additional commentary or markdown."
)


search_agent = Agent(
    name="search_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_search_instructions,
    tools=[google_search],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 1000)),
    ),
)


_writer_instructions = (
    "You help persist visa search results into the visa state.\n\n"
    "When the user provides a JSON object describing a visa search result, you MUST call the\n"
    "`record_visa_search_result` tool with the corresponding fields:\n"
    "- task_id (string)\n"
    "- summary (string)\n"
    "- processing_time_hint (string or null)\n"
    "- fee_hint (string or null)\n"
    "- notes (string or null)\n"
    "- sources (array of strings, URLs or human-readable source names)\n\n"
    "Do not call any other tools. In your final answer, briefly confirm the task_id you recorded."
)


visa_result_writer_agent = Agent(
    name="visa_result_writer_agent",
    model=Gemini(model=f"{_search_config.get('model', '')}"),
    instruction=_writer_instructions,
    tools=[record_visa_search_result],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_search_config.get("temperature", 0.0)),
        max_output_tokens=int(_search_config.get("max_tokens", 500)),
    ),
)
