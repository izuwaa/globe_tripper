# Dispatcher Agent (Concierge Intake)

Location: `src/agents/dispatcher_agent.py`  
Related tools: `src/tools/tools.py`, `src/tools/planning_tools.py`  
Related state: `src/state/planner_state.py`

The dispatcher agent is the conversational concierge that handles the first phase of a session: gathering enough information about the trip and the travelers to make planning possible.

---

## Responsibilities

- Carry on a natural‑language conversation with the user about:
  - Trip destination, origin, and dates.  
  - Number and type of travelers (adults, children, seniors), plus optional per‑traveler details.  
  - Preferences and constraints (budget, pace, interests, accessibility needs, etc.).  
- Call tools to keep `PlannerState` up to date as new information arrives.  
- Decide when intake is “good enough,” confirm with the user, and then mark the planner as ready for background planning.

It does **not** perform the planning itself; it only prepares the structured inputs and flips the status so planner/parallel planner and domain agents can take over.

---

## Tools Used

From `src/agents/dispatcher_agent.py`, the dispatcher is configured with three core tools:

- `update_trip_plan` (`src/tools/tools.py`)  
  - Reads the current `PlannerState` from the session.  
  - Applies any provided fields (destination, origin, dates, demographics, preferences, etc.) while leaving unspecified fields unchanged.  
  - Normalizes per‑traveler details and, if necessary, infers placeholder travelers from aggregate counts.  
  - Writes the updated planner state back via `save_planner_state`.

- `resolve_airports` (`src/tools/tools.py`)  
  - Uses the current trip details (primarily origin/destination cities) and external data to resolve concrete airport codes.  
  - Updates planner state fields like `origin_airport_code` and `destination_airport_code` so downstream tools can safely call flight/visa APIs that expect IATA codes.

- `mark_ready_for_planning` (`src/tools/planning_tools.py`)  
  - Checks `is_intake_complete(planner_state)` to ensure all required fields are present.  
  - If the planner is in `"intake"` status and intake is complete, flips `PlannerState.status` to `"planning"`.  
  - Returns a small JSON payload indicating success or why the transition was skipped/blocked.

The dispatcher’s instruction prompt (in `src/artifacts/dispatcher/instruction.md`) guides it to use these tools instead of trying to “remember” state in free‑form text.

---

## Interaction Pattern

In the interactive CLI (`run.py -> main()`), the dispatcher agent is wired into a `Runner` from `google-adk`:

- Each user turn is passed as `genai_types.Content` to `dispatcher_agent`.  
- The agent asks clarifying questions and calls tools as needed.  
- After each turn, the app prints a debug view of the current `PlannerState`, so you can see how the dispatcher is filling things in.

Once `PlannerState.status` becomes `"planning"`, the app kicks off the background planning pipelines (visa, flights, accommodation, activities) for that session.

---

## Extending the Dispatcher

Typical ways to extend the dispatcher include:

- Adding new planner fields (e.g., loyalty programs, special event constraints) and exposing them as parameters on `update_trip_plan`.  
- Enriching `resolve_airports` (or adding related tools) for more robust place/airport resolution.  
- Adjusting intake rules in `is_intake_complete` to support new required fields.  
- Tweaking the dispatcher’s instruction file to adjust tone, question ordering, or decision criteria for moving into planning.

