# Flight Agents (Planning & Search)

Location:  
- Planner: `src/agents/flight_agent.py`  
- Search: `src/agents/flight_search_agent.py`  
Related tools: `src/tools/tools.py` (flight helpers, search tools)  
Related state: `src/state/flight_state.py`, `src/state/planner_state.py`, `src/state/visa_state.py`

The flight agents work together to plan visa‑aware flight options for each trip. One agent (`flight_agent`) focuses on deriving `FlightSearchTask` objects and applying results, while others focus on calling external flight search APIs and normalizing the options.

---

## `flight_agent` – Flight Planning Specialist

Defined in `src/agents/flight_agent.py`.

Responsibilities:

- Derive `FlightSearchTask` entries for each origin→destination group of travelers, using visa‑aware recommended dates when available.  
- Call `derive_flight_search_tasks` exactly once per run to ensure the `FlightState` has the right set of tasks.  
- Provide a short narrative about which groups were planned and how visa timelines influenced dates.

Key tools:

- `derive_flight_search_tasks` (`src/tools/tools.py`)  
  - Reads `PlannerState` and `VisaState`.  
  - Groups travelers into origin→destination clusters.  
  - Adjusts departure/return dates if visa processing implies a later earliest safe departure.  
  - Creates `FlightSearchTask` instances with prompts and metadata, and writes them into `FlightState`.

There are also two closely related “apply” agents:

- `flight_apply_agent`  
  - Calls `apply_flight_search_results` exactly once.  
  - Intended to run after search results have been recorded.  
  - Produces a human‑readable confirmation that the results were applied.

- `flight_apply_tool_agent`  
  - Tool‑only variant that calls `apply_flight_search_results` without generating natural‑language text.  
  - Used as a deterministic fallback when we want to guarantee that `FlightState` has been updated even if the main agent fails to call the tool.

`apply_flight_search_results` itself:

- Reads the current `FlightState`, including `FlightSearchTask` and `FlightSearchResult` entries.  
- Derives `TravelerFlightChoice` objects (per‑traveler chosen option + alternates).  
- Populates `FlightState.overall_summary` with a concise description of the chosen strategy.

---

## Flight Search Agents

Defined in `src/agents/flight_search_agent.py`.

### `flight_search_tool_agent`

- Type: tool‑only `Agent`.  
- Tool: `searchapi_google_flights`.  
- Responsibility: given a single flight search task (JSON payload with origin, destination, dates, passengers, cabin), call `searchapi_google_flights` exactly once and return the raw tool output (no summarization).

This agent isolates the external API call so that other agents and pipelines can focus on interpretation and state updates.

### `flight_search_agent`

- Type: LLM‑backed `Agent`.  
- Tool: `record_flight_search_result`.  
- Responsibility:
  - Take a normalized list of options from the tool layer plus search context.  
  - Choose up to three canonical options (cheapest, fastest, balanced).  
  - Call `record_flight_search_result` exactly once with:
    - `task_id`.  
    - A human‑readable `summary`.  
    - An array of canonical options with prices, times, stops, etc.  
    - Hints like `best_price_hint`, `best_time_hint`, `cheap_but_long_hint`, and a `recommended_option_label`.  
    - A `chosen_option_type` and `selection_reason`.

### `flight_result_writer_agent`

- Type: helper `Agent`.  
- Tool: `record_flight_search_result`.  
- Responsibility: when given a JSON description of a search result, call `record_flight_search_result` with those fields. This is useful as a fallback or for flows where the LLM returns JSON directly.

All three agents converge on `record_flight_search_result`, which writes normalized options and summaries into `FlightState.search_results`.

---

## Role in the Overall Flow

From `run.py`, flight planning/search generally proceeds as follows:

1. `flight_agent` runs and calls `derive_flight_search_tasks` to populate `FlightSearchTask` entries.  
2. `run_flight_search_pipeline`:
   - Uses `flight_search_tool_agent` to call the external flight search API for each task.  
   - Uses `flight_search_agent` to normalize options and call `record_flight_search_result`.  
   - Handles fallback behaviors if the model fails to call the tool.  
3. `run_flight_apply_pipeline` (part of the broader `run_flight_pipeline`) calls `flight_apply_agent` and, if needed, `flight_apply_tool_agent` to ensure `FlightState.overall_summary` and `traveler_flights` are populated.

Other parts of the system (e.g., accommodation and activities) rely on these flight choices to anchor check‑in/check‑out times and daily itineraries.

---

## Extending the Flight Agents

Ways to extend or customize flight behavior:

- Add support for multi‑city or open‑jaw trips by enriching `FlightSearchTask` and the derivation logic.  
- Incorporate additional constraints into `derive_flight_search_tasks` (preferred airlines, cabin classes, maximum stops, etc.).  
- Add new tools for alternative flight data providers and expose them via new tool‑only agents.  
- Enhance `apply_flight_search_results` to compute more nuanced summaries (e.g., CO₂ estimates, minimum connection times, layover city preferences).

