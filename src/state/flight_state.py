from typing import List, Optional

from pydantic import BaseModel, Field


class FlightSearchTask(BaseModel):
    """
    Structured description of a flight search to perform for one or more travelers.
    """

    task_id: str = Field(
        ...,
        description="Unique identifier for this flight search task within the session.",
    )
    traveler_indexes: List[int] = Field(
        default_factory=list,
        description="Indexes into PlannerState.demographics.travelers that this task covers.",
    )

    origin_city: Optional[str] = Field(
        default=None,
        description="Origin city or airport code for this leg.",
    )
    destination_city: Optional[str] = Field(
        default=None,
        description="Destination city or airport code for this leg.",
    )

    original_departure_date: Optional[str] = Field(
        default=None,
        description="User's originally requested departure date (ISO string).",
    )
    original_return_date: Optional[str] = Field(
        default=None,
        description="User's originally requested return date (ISO string), if round-trip.",
    )
    recommended_departure_date: Optional[str] = Field(
        default=None,
        description="Visa-aware recommended departure date, if different from original.",
    )
    recommended_return_date: Optional[str] = Field(
        default=None,
        description="Visa-aware recommended return date, adjusted to preserve trip length where possible.",
    )
    visa_timeline_reason: Optional[str] = Field(
        default=None,
        description="Explanation of why recommended dates differ from original based on visa timelines.",
    )

    cabin_preference: Optional[str] = Field(
        default=None,
        description="Preferred cabin (economy, premium, business, first).",
    )
    budget_mode: Optional[str] = Field(
        default=None,
        description="Budget mode copied from PlannerState.preferences.budget_mode.",
    )
    flexibility_hint: Optional[str] = Field(
        default=None,
        description="Notes on date flexibility (e.g. ±3 days, one-way).",
    )

    prompt: Optional[str] = Field(
        default=None,
        description="Natural-language flight search description used by a search-focused agent.",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Short machine-readable label for this search (e.g. 'flight_options_lookup').",
    )


class FlightSearchResult(BaseModel):
    """
    Normalized result of a flight search for one FlightSearchTask.
    """

    task_id: str = Field(
        ...,
        description="ID of the FlightSearchTask this result corresponds to.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Final query string sent to the search tool or external API.",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Concise summary of typical routes, operators, and patterns.",
    )
    best_price_hint: Optional[str] = Field(
        default=None,
        description="Typical lowest reasonable price range per traveler.",
    )
    best_time_hint: Optional[str] = Field(
        default=None,
        description="Typical fastest or most time-efficient option (duration, stops).",
    )
    cheap_but_long_hint: Optional[str] = Field(
        default=None,
        description="Description of the cheapest but significantly longer options.",
    )
    recommended_option_label: Optional[str] = Field(
        default=None,
        description="Short label describing the recommended 'balanced' option.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes or caveats relevant for these flights.",
    )


class FlightState(BaseModel):
    """
    Container for all flight planning outputs for a given session.
    """

    search_tasks: List[FlightSearchTask] = Field(
        default_factory=list,
        description="Pending or completed flight search tasks to be run by a search agent.",
    )
    search_results: List[FlightSearchResult] = Field(
        default_factory=list,
        description="Normalized results from the flight search agent for each task.",
    )
    overall_summary: Optional[str] = Field(
        default=None,
        description="High-level summary of flight options and cost implications for the whole party.",
    )

