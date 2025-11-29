from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class FlightOption(BaseModel):
    """
    Structured representation of a single canonical flight option
    (e.g. cheapest, fastest, or balanced) for a given search task.
    """

    option_type: Literal["cheapest", "fastest", "balanced"] = Field(
        ...,
        description="Canonical bucket for this option.",
    )
    airlines: List[str] = Field(
        default_factory=list,
        description="Primary airlines operating this itinerary.",
    )

    currency: Optional[str] = Field(
        default="USD",
        description="Currency for all price fields.",
    )
    price_per_ticket_low: Optional[float] = Field(
        default=None,
        description="Lower bound estimate of price per ticket.",
    )
    price_per_ticket_high: Optional[float] = Field(
        default=None,
        description="Upper bound estimate of price per ticket.",
    )
    total_price_low: Optional[float] = Field(
        default=None,
        description="Lower bound estimate for all travelers on this task.",
    )
    total_price_high: Optional[float] = Field(
        default=None,
        description="Upper bound estimate for all travelers on this task.",
    )

    outbound_departure: Optional[str] = Field(
        default=None,
        description="Outbound departure datetime (ISO 8601, visa-aware).",
    )
    outbound_arrival: Optional[str] = Field(
        default=None,
        description="Outbound arrival datetime (ISO 8601).",
    )
    return_departure: Optional[str] = Field(
        default=None,
        description="Return departure datetime (ISO 8601).",
    )
    return_arrival: Optional[str] = Field(
        default=None,
        description="Return arrival datetime (ISO 8601).",
    )

    outbound_duration_hours: Optional[float] = Field(
        default=None,
        description="Approximate outbound block time in hours.",
    )
    return_duration_hours: Optional[float] = Field(
        default=None,
        description="Approximate return block time in hours.",
    )
    total_outbound_duration_minutes: Optional[int] = Field(
        default=None,
        description="Total outbound duration in minutes (all segments combined).",
    )
    total_return_duration_minutes: Optional[int] = Field(
        default=None,
        description="Total return duration in minutes (all segments combined), if applicable.",
    )
    total_trip_duration_minutes: Optional[int] = Field(
        default=None,
        description="Total trip duration in minutes (outbound + return).",
    )
    outbound_stops: Optional[int] = Field(
        default=None,
        description="Number of stops on the outbound leg.",
    )
    return_stops: Optional[int] = Field(
        default=None,
        description="Number of stops on the return leg.",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Free-form notes (e.g. baggage caveats, typical hubs).",
    )


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
    options: List[FlightOption] = Field(
        default_factory=list,
        description="Structured candidate options (cheapest/fastest/balanced) with numeric cost/time fields.",
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
    chosen_option_type: Optional[Literal["cheapest", "fastest", "balanced"]] = Field(
        default=None,
        description="Which canonical option type was ultimately selected for this task, if any.",
    )
    selection_reason: Optional[str] = Field(
        default=None,
        description="Short explanation of why the chosen option type was selected (e.g. economy vs luxury tradeoffs).",
    )


class TravelerFlightChoice(BaseModel):
    """
    Per-traveler view of a chosen flight option (plus alternatives) derived from
    FlightSearchResult entries.
    """

    traveler_index: int = Field(
        ...,
        description="Index into PlannerState.demographics.travelers.",
    )
    task_id: str = Field(
        ...,
        description="ID of the FlightSearchTask this choice came from.",
    )

    summary: Optional[str] = Field(
        default=None,
        description="Summary of routes/durations/airlines relevant to this traveler.",
    )
    best_price_hint: Optional[str] = Field(
        default=None,
        description="Price hint scoped to this traveler's task.",
    )
    best_time_hint: Optional[str] = Field(
        default=None,
        description="Time hint scoped to this traveler's task.",
    )
    cheap_but_long_hint: Optional[str] = Field(
        default=None,
        description="Description of cheap but long options for this traveler's task.",
    )
    recommended_option_label: Optional[str] = Field(
        default=None,
        description="Label for the recommended 'balanced' option for this traveler.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional caveats or remarks for this traveler's flights.",
    )

    chosen_option_type: Optional[Literal["cheapest", "fastest", "balanced"]] = Field(
        default=None,
        description="Which canonical option type was ultimately selected for this traveler.",
    )
    selection_reason: Optional[str] = Field(
        default=None,
        description="Short explanation of why the chosen option type was selected.",
    )

    chosen_option: Optional[FlightOption] = Field(
        default=None,
        description="The selected FlightOption for this traveler (if any).",
    )
    other_options: List[FlightOption] = Field(
        default_factory=list,
        description="Alternative FlightOptions that were considered but not selected.",
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
    traveler_flights: List[TravelerFlightChoice] = Field(
        default_factory=list,
        description="Per-traveler view of chosen flights and alternatives.",
    )
