# Agents Overview

This directory contains documentation for the main agents that make up the Globe Tripper AI travel concierge.

At a high level, agents fall into a few categories:

- **Concierge & orchestration**
  - `dispatcher_agent` – conversational intake concierge that collects trip details and preferences and marks the planner ready.  
  - `planner_root_agent` – high‑level planning orchestrator that calls domain agents as tools.  
  - `parallel_planner_agent` – runs multiple domain agents (visa, flights, and later more) in parallel.

- **Domain specialists**
  - `visa_agent` – derives visa requirements, creates visa search tasks, and applies search results into `VisaState`.  
  - `flight_agent` and friends – derive flight search tasks, call flight search tools, and apply results into `FlightState`.  
  - `accommodation_agent` and friends – derive accommodation search tasks, call accommodation tools, and apply results into `AccommodationState`.  
  - `activity_agent` and friends – derive activity search tasks, generate itineraries, and apply them into `ActivityState`.  
  - `bureaucracy_agent` – handles long‑form bureaucratic/visa text and updates planner state as needed.

- **Search & summary**
  - `search_agent` – web search assistant used for visa and other research flows.  
  - `trip_summary_agent` – reads consolidated state and produces a user‑friendly trip summary.

For deeper detail on individual agents, start with:

- `docs/agents/dispatcher.md` – intake concierge.  
- `docs/agents/planner.md` – root planner + parallel planner.  
- `docs/agents/visa.md` – visa specialist.  
- `docs/agents/flight.md` – flight planning and flight search agents.

