import os

from google.adk.agents import Agent, ParallelAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types as genai_types
from google.adk.tools import AgentTool

from src.agents.visa_agent import visa_agent
from src.agents.flight_agent import flight_agent


agent_config_path = os.path.join(os.path.dirname(__file__), "../config/agents.yaml")
planner_instructions_path = os.path.join(
    os.path.dirname(__file__), "../artifacts/planner/instruction.md"
)


with open(agent_config_path, "r") as f:
    import yaml

    agent_configs = yaml.safe_load(f)

with open(planner_instructions_path, "r") as f:
    _planner_instructions = f.read()

_planner_config = agent_configs.get("planner", {})


# Parallel planner: runs its sub‑agents (currently only visa_agent)
# concurrently. More domain agents (itinerary, flights, transport, costs)
# can be appended to this list over time.
parallel_planner_agent = ParallelAgent(
    name="parallel_planner_agent",
    sub_agents=[visa_agent, flight_agent],
)


# Coordinator/root planner agent: orchestrates planning by calling
# sub‑agents (including the visa_agent) as tools. This is what you
# would typically attach to a Runner for high‑level planning flows.
planner_root_agent = Agent(
    name="planner_root_agent",
    model=LiteLlm(
        model=f"{_planner_config.get('provider', '')}/{_planner_config.get('model', '')}"
    ),
    instruction=_planner_instructions,
    tools=[
        AgentTool(visa_agent),
        AgentTool(parallel_planner_agent),
        AgentTool(flight_agent),
    ],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=float(_planner_config.get("temperature", 0.2)),
        max_output_tokens=int(_planner_config.get("max_tokens", 1000)),
    ),
)
