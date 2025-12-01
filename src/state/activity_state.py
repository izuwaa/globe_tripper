from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class ActivityOption(BaseModel):
    """
    Structured representation of a single activity, attraction, or experience
    that could be added to an itinerary.
    """

    name: str = Field(..., description="Display name of the activity or place.")
    category: Optional[str] = Field(
        default=None,
        description="High-level category, e.g. museum, park, restaurant, tour.",
    )

    location_label: Optional[str] = Field(
        default=None,
        description="Human-friendly location label (e.g. 'Near Hyde Park').",
    )
    neighborhood: Optional[str] = Field(
        default=None,
        description="Neighborhood or area name, if known.",
    )
    city: Optional[str] = Field(
        default=None,
        description="City for this activity.",
    )
    country: Optional[str] = Field(
        default=None,
        description="Country for this activity.",
    )

    latitude: Optional[float] = Field(
        default=None,
        description="Approximate latitude, if available.",
    )
    longitude: Optional[float] = Field(
        default=None,
        description="Approximate longitude, if available.",
    )

    duration_minutes: Optional[int] = Field(
        default=None,
        description="Typical visit duration in minutes, if known.",
    )

    price_per_person_low: Optional[float] = Field(
        default=None,
        description="Lower bound estimate of price per person.",
    )
    price_per_person_high: Optional[float] = Field(
        default=None,
        description="Upper bound estimate of price per person.",
    )
    currency: Optional[str] = Field(
        default=None,
        description="Currency for price fields, when known.",
    )

    is_free: Optional[bool] = Field(
        default=None,
        description="True if the activity is generally free to visit.",
    )
    ticket_required: Optional[bool] = Field(
        default=None,
        description="True if a ticket is generally required for entry.",
    )
    booking_required: Optional[bool] = Field(
        default=None,
        description="True if advance booking is typically required.",
    )

    suitable_for_adults: Optional[bool] = Field(
        default=None,
        description="True if this activity is suitable for adults.",
    )
    suitable_for_children: Optional[bool] = Field(
        default=None,
        description="True if this activity is suitable for children.",
    )
    suitable_for_mobility_issues: Optional[bool] = Field(
        default=None,
        description="True if generally accessible for limited mobility.",
    )

    opening_hours_hint: Optional[str] = Field(
        default=None,
        description="Short hint about opening days/hours.",
    )
    distance_from_base_hint: Optional[str] = Field(
        default=None,
        description="Short text describing distance from the main accommodation or city center.",
    )

    rating: Optional[float] = Field(
        default=None,
        description="Average rating if available.",
    )
    rating_count: Optional[int] = Field(
        default=None,
        description="Number of ratings underlying the rating.",
    )

    url: Optional[str] = Field(
        default=None,
        description="Primary URL to view details or book.",
    )
    booking_url: Optional[str] = Field(
        default=None,
        description="Direct booking URL if distinct from url.",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Additional caveats or highlights for this activity.",
    )


class ActivitySearchTask(BaseModel):
    """
    Description of an activity search to perform for one or more travelers.
    """

    task_id: str = Field(
        ...,
        description="Unique identifier for this activity search task within the session.",
    )
    traveler_indexes: List[int] = Field(
        default_factory=list,
        description="Indexes into PlannerState.demographics.travelers covered by this task.",
    )

    location: Optional[str] = Field(
        default=None,
        description="City or area to search in (e.g. 'London', 'Shoreditch').",
    )
    date_start: Optional[str] = Field(
        default=None,
        description="Start of the date range for this task (ISO date).",
    )
    date_end: Optional[str] = Field(
        default=None,
        description="End of the date range for this task (ISO date).",
    )

    interests: List[str] = Field(
        default_factory=list,
        description="High-level interests to emphasize (e.g. museums, parks, food).",
    )
    must_do: List[str] = Field(
        default_factory=list,
        description="Specific must-do items or attractions.",
    )
    nice_to_have: List[str] = Field(
        default_factory=list,
        description="Nice-to-have items or themes.",
    )

    budget_mode: Optional[str] = Field(
        default=None,
        description="Budget mode copied from PlannerState.preferences.budget_mode.",
    )

    prompt: Optional[str] = Field(
        default=None,
        description="Natural-language description used by an activity search agent.",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Short machine-readable label for this search (e.g. 'activity_options_lookup').",
    )


class ActivitySearchResult(BaseModel):
    """
    Normalized result of an activity search for one ActivitySearchTask.
    """

    task_id: str = Field(
        ...,
        description="ID of the ActivitySearchTask this result corresponds to.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Final query string sent to google_search or other tools.",
    )

    options: List[ActivityOption] = Field(
        default_factory=list,
        description="Structured candidate activities for this task.",
    )

    summary: Optional[str] = Field(
        default=None,
        description="Concise summary of the types of activities and neighborhoods discovered.",
    )
    budget_hint: Optional[str] = Field(
        default=None,
        description="Hint about typical activity costs for this task.",
    )
    family_friendly_hint: Optional[str] = Field(
        default=None,
        description="Hint about how family-friendly the activity set is.",
    )
    neighborhood_hint: Optional[str] = Field(
        default=None,
        description="Guidance on neighborhoods / clusters suitable for this task.",
    )


class DayItineraryItem(BaseModel):
    """
    A single scheduled activity in the day-by-day itinerary.
    """

    date: str = Field(
        ...,
        description="ISO date for this itinerary item.",
    )
    slot: Literal["morning", "afternoon", "evening"] = Field(
        ...,
        description="Coarse time-of-day slot.",
    )
    traveler_indexes: List[int] = Field(
        default_factory=list,
        description="Travelers participating in this activity.",
    )
    task_id: str = Field(
        ...,
        description="ActivitySearchTask ID this item came from.",
    )
    activity: ActivityOption = Field(
        ...,
        description="The selected ActivityOption to schedule.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes (e.g. tickets, timing, transport).",
    )


class ActivityState(BaseModel):
    """
    Container for all activity / itinerary planning outputs for a given session.
    """

    search_tasks: List[ActivitySearchTask] = Field(
        default_factory=list,
        description="Pending or completed activity search tasks.",
    )
    search_results: List[ActivitySearchResult] = Field(
        default_factory=list,
        description="Normalized results from activity search agents.",
    )
    day_plan: List[DayItineraryItem] = Field(
        default_factory=list,
        description="Coarse day-by-day itinerary items across the trip.",
    )
    overall_summary: Optional[str] = Field(
        default=None,
        description="High-level summary of the planned itinerary and key themes.",
    )

