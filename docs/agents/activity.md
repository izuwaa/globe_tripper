# Activity & Itinerary Agents

Location: `src/agents/activity_agent.py`  
Related tools: `src/tools/tools.py` (activity helpers, itinerary tools)  
Related state: `src/state/activity_state.py`, `src/state/planner_state.py`, `src/state/flight_state.py`, `src/state/accommodation_state.py`

The activity and itinerary agents plan what travelers will actually do on their trip. They start from interests and dates, derive `ActivitySearchTask` entries, search for candidate activities, normalize them into `ActivityOption` objects, and finally stitch them into a day‑by‑day itinerary (`DayItineraryItem` list in `ActivityState`).

---

## Activity Planning & Search

Several agents defined in `src/agents/activity_agent.py` work together:

- `activity_agent` – planning specialist that derives `ActivitySearchTask` entries once the core trip structure is known.  
- `activity_search_agent` – search summarization agent that interprets search results and records normalized options.  
- `activity_result_writer_agent` – helper agent that takes JSON‑like search outputs and calls a writer tool.  
- `activity_apply_agent` – applies search results into higher‑level summaries and prepares for itinerary building.

Key tools (from `src/tools/tools.py`):

- `derive_activity_search_tasks`  
  - Reads `PlannerState` (dates, destination, preferences), `ActivityState`, and potentially `AccommodationState`.  
  - Creates `ActivitySearchTask` instances with fields like `location`, `date_start`, `date_end`, `interests`, `must_do`, `nice_to_have`, and `budget_mode`.  
  - Writes them into `ActivityState.search_tasks`.

- `record_activity_search_result`  
  - Called by search/summarization agents to store `ActivitySearchResult` objects (normalized options plus hints) into `ActivityState.search_results`.

- `apply_activity_search_results`  
  - Reads `ActivitySearchTask` and `ActivitySearchResult` entries.  
  - Prepares higher‑level summaries and, where appropriate, hints that are useful for itinerary planning.

Search agents also use `google_search` (from `google.adk.tools`) to query the web for specific attractions, tours, and family‑friendly spots, building structured `ActivityOption` entries that include names, neighborhoods, approximate prices, ratings, and URLs.

---

## Day‑by‑Day Itinerary Agents

Once there are enough activity options and a clear sense of dates, two additional agents help build and persist the day‑by‑day itinerary:

### `day_itinerary_search_agent`

- Type: LLM‑backed `Agent`.  
- Tools: `google_search`.  
- Instruction highlights:
  - Receives a small “slice” of the trip (a few days, travelers, preferences, and activity suggestions).  
  - Uses heuristics around arrival/departure days, full days, pace, and daily rhythm to propose realistic schedules for each day (morning/afternoon/evening).  
  - May call `google_search` to find concrete examples of parks, museums, restaurants, etc.  
  - Returns a compact, strictly valid JSON object with an `items` array, where each item is:
    - `date` (ISO date).  
    - `slot` (`morning` | `afternoon` | `evening`).  
    - `name`, `notes`, optional `task_id`, `neighborhood`, `city`, `url`, and `traveler_indexes`.

This agent focuses on generating a candidate itinerary for a small set of days, not on writing back to state directly.

### `activity_itinerary_agent`

- Type: LLM‑backed `Agent`.  
- Tool: `record_day_itinerary`.  
- Responsibility:
  - Takes the JSON output describing itinerary items (for a subset of days).  
  - Calls `record_day_itinerary` exactly once with:
    - A list of items (date, slot, name, optional notes/task_id/traveler_indexes/neighborhood/city/url).  
    - An `overall_summary` describing the itinerary for those days.  
  - Does not return the itinerary itself in text; the canonical output is the tool call.

`record_day_itinerary` then writes `DayItineraryItem` entries into `ActivityState.day_plan`, which downstream components (like `trip_summary_agent`) use to describe the trip.

---

## Role in the Overall Flow

From `run.py`, the activity pipeline roughly does:

1. Call `derive_activity_search_tasks` via `activity_agent` once the planner, visa, flight, and accommodation state are reasonably stable.  
2. For each `ActivitySearchTask`, call search/summarization agents to populate `ActivityState.search_results` with normalized options and hints.  
3. Use `day_itinerary_search_agent` to generate realistic per‑day schedules (in small slices), optionally leveraging activity hints and google_search lookups.  
4. Use `activity_itinerary_agent` to persist these schedules into `ActivityState.day_plan`.  
5. Finally, `trip_summary_agent` reads the resulting `ActivityState` (including `sample_days` constructed in `run.py`) to synthesize a human‑readable view of the itinerary.

---

## Extending the Activity & Itinerary Agents

Ideas for extension:

- Enrich `ActivityOption` with more metadata (e.g., typical duration, age restrictions, ticketing notes) and have search agents populate those fields.  
- Adjust itinerary heuristics to account for more complex patterns (multi‑city trips, split stays, explicit rest days, event days).  
- Add tools for specific providers (e.g., ticketing APIs, tour marketplaces) and corresponding tool‑only agents.  
- Tailor prompts to different traveler profiles (solo, couples, families with toddlers, seniors) or to different destinations (city breaks vs. beach vacations).  
- Add a “regenerate day” flow where the user can ask to re‑plan a single day or subset of days based on updated preferences.

