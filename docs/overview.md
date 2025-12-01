# Globe Tripper Overview

Globe Tripper is an AI travel concierge that coordinates a team of specialist agents to plan complex, mostly international trips end‑to‑end. It is built on top of the Google Agent Development Kit (ADK) and organized around a shared planner state, domain‑specific states (visa, flights, accommodation, activities), and a small set of orchestrator agents.

For the narrative and business context behind the project, see `about-this-agent.md`. For a practical “how to run it” guide, see `README.md`.

---

## Core Concepts

- **Concierge** – the conversational front door that collects trip details and preferences, and decides when to move from intake to planning. Implemented by `dispatcher_agent`.  
- **Planner** – orchestrator agents (`planner_root_agent` and `parallel_planner_agent`) that call domain specialists (visa, flights, accommodation, activities) as tools or as parallel sub‑agents.  
- **Domain specialists** – agents that own one part of the trip (e.g., `visa_agent`, `flight_agent`, `accommodation_agent`, `activity_agent`), often backed by tools that talk to external APIs.  
- **State** – typed Pydantic models in `src/state/` (`PlannerState`, `VisaState`, `FlightState`, `AccommodationState`, `ActivityState`) that store the evolving trip plan across the whole conversation.  
- **Tools** – functions in `src/tools/` that agents call to mutate state and/or hit external APIs (e.g., Google Flights, accommodation APIs, web search).

The key design idea is that agents do not pass around long histories or ad‑hoc JSON blobs; they read and write well‑typed state, which makes it easier to reason about behavior, test it, and add new agents over time.

---

## High‑Level Flow

1. **Intake**  
   - User talks to the CLI concierge (`run.py -> main()`).  
   - `dispatcher_agent` uses tools like `update_trip_plan`, `resolve_airports`, and `mark_ready_for_planning` to populate `PlannerState` with trip details, demographics, and preferences.  
   - Once `is_intake_complete(planner_state)` is true and the user confirms, `mark_ready_for_planning` flips `PlannerState.status` from `"intake"` to `"planning"`.

2. **Planning**  
   - When planner status becomes `"planning"`, the app kicks off background planning pipelines (visa, flights, accommodation, activities) for the current session.  
   - Pipelines use domain agents (`visa_agent`, `flight_agent`, `accommodation_agent`, `activity_agent`, etc.) plus supporting search/tool agents to derive search tasks, call external APIs, normalize options, and write choices back into their respective state objects.  
   - In higher‑level flows, `planner_root_agent` and `parallel_planner_agent` can be used to orchestrate multiple domain agents at once, acting as a planning “brain” over the shared state.

3. **Summary / Handoff**  
   - Once the core pipelines have populated visa, flights, accommodation, and activities, `trip_summary_agent` reads a compact JSON view of all relevant state.  
   - It generates a structured natural‑language summary of the trip (constraints + recommended plan), suitable for handing off to the user or another system.

---

## Key Components at a Glance

- `src/agents/dispatcher_agent.py` – intake concierge that populates planner state and decides when to move into planning.  
- `src/agents/parallel_planner_agent.py` – defines `parallel_planner_agent` and `planner_root_agent`, which orchestrate domain specialists.  
- `src/agents/visa_agent.py`, `src/agents/flight_agent.py`, `src/agents/accommodation_agent.py`, `src/agents/activity_agent.py` – domain agents for visas, flights, stays, and activities/itinerary.  
- `src/agents/search_agent.py`, `src/agents/flight_search_agent.py`, `src/agents/accommodation_search_agent.py` – search‑focused agents that talk to external APIs and record results.  
- `src/agents/summary_agent.py` – summarizes the entire trip.  
- `src/state/*.py` – Pydantic models for shared planner state and each domain’s state.  
- `src/tools/tools.py`, `src/tools/planning_tools.py` – toolbox used by agents to manipulate state and reach external APIs.  
- `src/artifacts/*/instruction.md` – “system prompts” and instructions for each agent.

---

## Where to Go Next

If you are exploring or extending the system:

- Start with `docs/agents/dispatcher.md` to understand how intake works.  
- Then read `docs/agents/planner.md` for how the planner/parallel planner are intended to orchestrate visa, flights, and beyond.  
- Use the tests in `tests/` and the pipelines in `run.py` as concrete examples of how agents and tools are wired together.

