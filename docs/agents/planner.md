# Planner Agents (Root Planner & Parallel Planner)

Location: `src/agents/parallel_planner_agent.py`  
Related prompts: `src/artifacts/planner/instruction.md`  
Related config: `src/config/agents.yaml` (section: `planner`)

The planner agents are the “planning brain” of Globe Tripper. They sit between the intake dispatcher and the domain specialists (visa, flights, accommodation, activities) and are responsible for deciding which specialist to call, in what order and combination, and how to summarize the outcome for the user.

There are two closely related planner agents defined in `parallel_planner_agent.py`:

- `parallel_planner_agent` – a `ParallelAgent` that runs multiple sub‑agents concurrently.  
- `planner_root_agent` – a standard `Agent` that orchestrates other agents by calling them as tools.

---

## `parallel_planner_agent`

Definition (simplified):

- Type: `ParallelAgent` (from `google.adk.agents`).  
- Sub‑agents (current):  
  - `visa_agent` – derives and applies visa requirements and search tasks.  
  - `flight_agent` – derives visa‑aware flight search tasks and orchestrates the flight search pipeline.

Behavior:

- When invoked, the parallel planner runs its sub‑agents concurrently.  
- Each sub‑agent reads and writes its own slice of shared state (`PlannerState`, `VisaState`, `FlightState`, etc.).  
- This setup is designed to scale: you can append additional sub‑agents over time (e.g., itinerary, accommodation, transport, budget optimizer) without changing the orchestration pattern.

Intended usage:

- Use `parallel_planner_agent` when you want to initiate “full planning” across domains at once—for example, after intake is complete and the user has confirmed they want the concierge to plan the trip.

---

## `planner_root_agent`

Definition (simplified):

- Type: `Agent` (from `google.adk.agents`).  
- Model: configured via the `planner` section in `src/config/agents.yaml` (provider/model/temperature/max_tokens).  
- Instruction: loaded from `src/artifacts/planner/instruction.md`.  
- Tools:
  - `AgentTool(visa_agent)`  
  - `AgentTool(parallel_planner_agent)`  
  - `AgentTool(flight_agent)`

Responsibilities:

- Interpret higher‑level user requests such as “plan my trip,” “update the plan,” or “check my visas and flights.”  
- Decide whether to:
  - Call `visa_agent` directly (e.g., when the user asks about visas).  
  - Call `parallel_planner_agent` to run multiple planning specialists in parallel.  
  - Call `flight_agent` directly to refine or re‑plan flight options.  
- Summarize the combined results back to the user in a clear, narrative form, highlighting trade‑offs and next steps.

The behavior and style are primarily driven by `src/artifacts/planner/instruction.md`, which positions this agent as the “Planning Orchestrator” for Globe Tripper.

---

## Relationship to Pipelines in `run.py`

In `run.py`, there are explicit pipelines (`run_visa_search_pipeline`, `run_flight_pipeline`, `run_accommodation_pipeline`, `run_activity_pipeline`) that:

- Read the current session’s state.  
- Call domain agents and tool‑only agents directly via `Runner`.  
- Persist and print state as they go.

These pipelines are complementary to the planner agents:

- Pipelines provide deterministic, inspectable flows that are easy to debug and test.  
+- Planner agents provide a more declarative, “ask the orchestrator to do the right thing” interface suitable for higher‑level automation or future API endpoints.

Depending on your integration, you can:

- Call the pipelines directly (as `run.py` does today) for tight control.  
- Or attach `planner_root_agent` to a `Runner` in your own application and let it decide when/how to call `visa_agent`, `parallel_planner_agent`, and `flight_agent`.

---

## Extending the Planner

Common extension paths:

- Add more sub‑agents to `parallel_planner_agent` (e.g., itinerary, accommodation, ground transport, cost optimizer) as they mature.  
- Expose additional planner‑level tools on `planner_root_agent` (for example, a tool that returns a machine‑readable view of the current plan).  
- Update `src/artifacts/planner/instruction.md` to teach the planner how to handle new domains or business rules (corporate travel policies, loyalty constraints, etc.).  
- Adjust `src/config/agents.yaml` to swap models or providers as needed for performance/cost/quality.

