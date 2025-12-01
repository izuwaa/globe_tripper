# Accommodation Agents (Planning & Search)

Location:  
- Planner: `src/agents/accommodation_agent.py`  
- Search & apply: `src/agents/accommodation_search_agent.py` (tool + summarization), `src/agents/accommodation_agent.py` (apply/choice agents)  
Related tools: `src/tools/tools.py` (accommodation helpers, search tools)  
Related state: `src/state/accommodation_state.py`, `src/state/planner_state.py`, `src/state/flight_state.py`

The accommodation agents plan where travelers will stay, using flight and planner state to derive realistic check‑in/check‑out windows and then searching for suitable options. One agent focuses on deriving `AccommodationSearchTask` objects, others call external APIs, normalize options, and apply choices back into `AccommodationState`.

---

## `accommodation_agent` – Accommodation Planning Specialist

Defined in `src/agents/accommodation_agent.py`.

Responsibilities:

- Derive `AccommodationSearchTask` entries for the trip once flights and core planner details are known.  
- Use trip details, traveler counts, and flight arrival/departure times to choose appropriate check‑in/check‑out dates.  
- Call `derive_accommodation_search_tasks` exactly once per planning run.  
- Provide a short narrative about which location(s) were planned and any date adjustments based on flights.

Key tool:

- `derive_accommodation_search_tasks` (`src/tools/tools.py`)  
  - Reads `PlannerState` and `FlightState`.  
  - Determines search `location`, `check_in_date`, and `check_out_date` and groups travelers into tasks.  
  - Copies relevant preferences such as `budget_mode`, `preferred_types`, and neighborhood hints.  
  - Creates `AccommodationSearchTask` instances and writes them into `AccommodationState`.

---

## Apply & Choice Agents

Also defined in `src/agents/accommodation_agent.py`:

### `accommodation_apply_agent`

- Type: LLM‑backed `Agent`.  
- Tool: `apply_accommodation_search_results`.  
- Responsibility:
  - After search results have been recorded, call `apply_accommodation_search_results` exactly once.  
  - Ensure `AccommodationState.overall_summary` is populated and that per‑traveler accommodation choices are set.  
  - Provide a short confirmation describing how many tasks/results were processed.

### `accommodation_apply_tool_agent`

- Type: tool‑only `Agent`.  
- Tool: `apply_accommodation_search_results`.  
- Responsibility: deterministic fallback to apply search results without generating natural‑language text (useful when you want guaranteed state updates).

### `accommodation_choice_agent`

- Type: LLM‑backed `Agent`.  
- Tool: `record_traveler_accommodation_choice`.  
- Responsibility:
  - Given a small JSON payload describing `task_id`, `traveler_indexes`, `chosen_option_type` (e.g. `cheapest`, `best_location`, `family_friendly`, `balanced`, `luxury`), and optional notes, call `record_traveler_accommodation_choice` exactly once.  
  - Let downstream flows explicitly select a canonical option for a given traveler group.

`apply_accommodation_search_results` and `record_traveler_accommodation_choice` work against the typed models in `src/state/accommodation_state.py` (`AccommodationSearchTask`, `AccommodationSearchResult`, `AccommodationOption`, `TravelerAccommodationChoice`).

---

## Search Agents & External APIs

Search‑focused agents for accommodation live in `src/agents/accommodation_search_agent.py` (not shown here in full). They typically follow the same pattern as flight search:

- A tool‑only agent that calls an external hotel/rental API (using OpenAPI specs in `src/tools/*.yaml`).  
- A summarization agent that:
  - Reads normalized options.  
  - Chooses canonical options (cheapest, best location, family‑friendly, balanced, luxury).  
  - Calls a tool like `record_accommodation_search_result` to persist those options into `AccommodationState.search_results`.

This split lets you swap the external data source without touching the higher‑level planning logic.

---

## Role in the Overall Flow

From `run.py`, the accommodation pipeline generally looks like:

1. `accommodation_agent` runs once to derive `AccommodationSearchTask` entries.  
2. `run_accommodation_pipeline`:
   - Uses a tool‑only search agent to call the external accommodation API for each task.  
   - Uses a summarization agent to normalize options and record results.  
   - Creates stub results if the model fails to record them, to keep state consistent.  
3. `accommodation_apply_agent` (or `accommodation_apply_tool_agent`) runs to:
   - Apply the search results into traveler‑level choices.  
   - Populate `AccommodationState.overall_summary`.

Other parts of the system (activities/itineraries, trip summary) rely on these choices to anchor neighborhoods, budgets, and daily rhythms.

---

## Extending the Accommodation Agents

Common extension paths:

- Enhance `AccommodationOption` and related models with more attributes (e.g., bed types, accessibility features, breakfast included) and teach the tools to populate them.  
- Improve `derive_accommodation_search_tasks` to support multi‑stop itineraries or split‑stay trips (e.g., multiple cities).  
- Add new search tools for different accommodation providers and wire them into the tool‑only search agent.  
- Adjust the summarization logic to better reflect your brand’s positioning (e.g., more emphasis on design hotels vs. budget stays).

