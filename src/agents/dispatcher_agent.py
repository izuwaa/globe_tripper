import os
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from src.tools.tools import update_trip_plan, resolve_airports
from src.tools.planning_tools import mark_ready_for_planning
import yaml
from google.genai import types as genai_types

agent_config_path = os.path.join(os.path.dirname(__file__), '../config/agents.yaml')
agent_instructions_path = os.path.join(os.path.dirname(__file__), '../artifacts/dispatcher/instruction.md')

# read the configs
with open(agent_config_path, 'r') as f:
    agent_configs = yaml.safe_load(f)

# read the instructions
with open(agent_instructions_path, 'r') as f:
    _instructions = f.read()

_agent_config = agent_configs.get('dispatcher', {})


# The DispatcherAgent (intake agent)
dispatcher_agent = Agent(
    name="dispatcher_agent",
    model=LiteLlm(model=f"{_agent_config.get('provider', '')}/{_agent_config.get('model', '')}"),
    instruction=_instructions,
    tools=[update_trip_plan, resolve_airports, mark_ready_for_planning],
    generate_content_config=genai_types.GenerateContentConfig(
    temperature=float(_agent_config.get("temperature", 0.2)),
    max_output_tokens=int(_agent_config.get("max_tokens", 1000)),
    ),
)
