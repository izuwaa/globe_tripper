# Visa Agent

Location: `src/agents/visa_agent.py`  
Related tools: `src/tools/tools.py` (visa helpers, search state readers/writers)  
Related state: `src/state/visa_state.py`, `src/state/planner_state.py`

The visa agent is the domain specialist responsible for understanding visa requirements for each traveler, generating structured search tasks, and applying search results back into `VisaState`.

---

## Responsibilities

- Read the current `PlannerState` (destination, dates, travelers) in a safe, compact way.  
- Derive visa requirements per traveler and group them into `VisaSearchTask` objects where appropriate.  
- Work with search‑oriented agents/tools that call external web search to fill in details (processing times, fees, documents, etc.).  
- Apply those search results back into `VisaState` as structured `VisaRequirement` entries with human‑readable summaries.

The visa agent itself does not call web search directly; instead, it prepares and consumes structured tasks/results.

---

## Tools Used

From `src/agents/visa_agent.py`, the visa agent is configured with:

- `_visa_state_reader` (internal tool)  
  - Reads `PlannerState` via `get_planner_state(tool_context)` and returns a minimal JSON‑like structure with:
    - `destination` (trip destination).  
    - `start_date`, `end_date`.  
    - `travelers` (index, role, nationality, origin for each traveler).  
  - This is meant to give the agent a clean view of the information it needs without exposing raw session internals.

- `build_visa_search_prompt` (`src/tools/tools.py`)  
  - Uses the current `PlannerState` and `VisaState` to build prompts or skeletons for visa research, grouped by nationality and destination.  
  - Typically called when the visa agent needs to derive or refine search tasks.

- `apply_visa_search_results` (`src/tools/tools.py`)  
  - Reads existing `VisaSearchTask` and `VisaSearchResult` entries from `VisaState`.  
  - Applies search findings into per‑traveler `VisaRequirement` objects and updates `VisaState.overall_summary`.  
  - Provides a stable place where free‑form search output is normalized into structured state.

The visa agent’s instruction prompt lives in `src/artifacts/visa/instruction.md`, which describes when to call each tool and how to explain the outcome to the user.

---

## Role in the Overall Flow

Visa planning typically proceeds in three phases:

1. **Derive tasks**  
   - `visa_agent` uses `_visa_state_reader` and `build_visa_search_prompt` (or related tools) to populate `VisaSearchTask` entries in `VisaState`.  
   - Tasks are usually grouped by nationality and destination to reduce duplicate work.

2. **Perform search**  
   - `search_agent` and related pipeline code in `run.py` call a web search tool (via `google_search`) using the prompts the visa agent prepared.  
   - Results are recorded as `VisaSearchResult` objects.

3. **Apply results**  
   - `visa_agent` is called again (see `run_visa_search_pipeline` in `run.py`) and uses `apply_visa_search_results` to merge search findings into `VisaRequirement` objects and update `VisaState.overall_summary`.  
   - The agent also produces a natural‑language explanation of what each traveler needs.

Other agents, such as `flight_agent`, rely on visa timing (e.g., earliest safe departure date) when proposing flight plans, so visa planning is usually run early in the pipeline.

---

## Extending the Visa Agent

Typical extension points:

- Add more fields to `VisaRequirement` and `VisaSearchResult` (e.g., biometrics requirements, typical refusal reasons) and teach `apply_visa_search_results` to populate them.  
- Refine the grouping logic in the task derivation tools so that families with mixed nationalities are handled more optimally.  
- Expand the instruction file (`src/artifacts/visa/instruction.md`) with clearer policies around uncertain or conflicting online information.  
- Introduce additional tools for country‑specific data sources (e.g., official government APIs) and expose them to `visa_agent`.

