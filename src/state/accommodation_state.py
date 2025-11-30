from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class AccommodationOption(BaseModel):
    """
    Structured representation of a single accommodation option
    (hotel, vacation rental, hostel, etc.) for a given search task.
    """

    option_type: Literal[
        "cheapest",
        "best_location",
        "family_friendly",
        "balanced",
        "luxury",
    ] = Field(
        ...,
        description="Canonical bucket for this option.",
    )

    stay_type: Literal[
        "hotel",
        "vacation_rental",
        "bnb",
        "hostel",
        "apartment",
        "other",
    ] = Field(
        ...,
        description="Type of accommodation (e.g. hotel vs vacation rental).",
    )

    provider: Optional[str] = Field(
        default=None,
        description="Source system for this option (e.g. searchapi_hotels, searchapi_rentals).",
    )

    name: Optional[str] = Field(
        default=None,
        description="Display name of the property.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Short description or tagline summarizing the stay.",
    )

    location_label: Optional[str] = Field(
        default=None,
        description="Human-friendly location label (e.g. 'Near Hyde Park').",
    )
    neighborhood: Optional[str] = Field(
        default=None,
        description="Neighborhood name if available.",
    )
    city: Optional[str] = Field(
        default=None,
        description="City for this accommodation.",
    )
    country: Optional[str] = Field(
        default=None,
        description="Country for this accommodation.",
    )

    currency: Optional[str] = Field(
        default="USD",
        description="Currency for all price fields.",
    )
    nightly_price_low: Optional[float] = Field(
        default=None,
        description="Lower bound estimate of nightly price.",
    )
    nightly_price_high: Optional[float] = Field(
        default=None,
        description="Upper bound estimate of nightly price.",
    )
    total_price_low: Optional[float] = Field(
        default=None,
        description="Lower bound estimate for the full stay.",
    )
    total_price_high: Optional[float] = Field(
        default=None,
        description="Upper bound estimate for the full stay.",
    )

    rating: Optional[float] = Field(
        default=None,
        description="Average guest rating (e.g. 4.5).",
    )
    rating_count: Optional[int] = Field(
        default=None,
        description="Number of reviews underlying the rating.",
    )

    max_guests: Optional[int] = Field(
        default=None,
        description="Maximum number of guests supported by this option.",
    )
    bedrooms: Optional[int] = Field(
        default=None,
        description="Number of bedrooms, if applicable.",
    )
    beds: Optional[int] = Field(
        default=None,
        description="Number of beds, if applicable.",
    )
    bathrooms: Optional[float] = Field(
        default=None,
        description="Number of bathrooms (may be fractional for half-baths).",
    )

    amenities: List[str] = Field(
        default_factory=list,
        description="Key amenities (e.g. Wi‑Fi, kitchen, parking, pool).",
    )
    cancellation_policy: Optional[str] = Field(
        default=None,
        description="Short description of the cancellation/refund policy.",
    )

    url: Optional[str] = Field(
        default=None,
        description="Deep link to view or book this accommodation.",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Additional caveats or highlights for this option.",
    )


class AccommodationSearchTask(BaseModel):
    """
    Structured description of an accommodation search to perform for one
    or more travelers.
    """

    task_id: str = Field(
        ...,
        description="Unique identifier for this accommodation search task within the session.",
    )
    traveler_indexes: List[int] = Field(
        default_factory=list,
        description="Indexes into PlannerState.demographics.travelers that this task covers.",
    )

    location: Optional[str] = Field(
        default=None,
        description="City or area to search in (e.g. 'London', 'Shoreditch').",
    )
    check_in_date: Optional[str] = Field(
        default=None,
        description="Check‑in date as an ISO string.",
    )
    check_out_date: Optional[str] = Field(
        default=None,
        description="Check‑out date as an ISO string.",
    )

    budget_mode: Optional[str] = Field(
        default=None,
        description="Budget mode copied from PlannerState.preferences.budget_mode.",
    )
    preferred_types: List[str] = Field(
        default_factory=list,
        description="Preferred stay types (e.g. ['hotel', 'vacation_rental']).",
    )

    neighborhood_preferences: List[str] = Field(
        default_factory=list,
        description="Neighborhoods or areas to prioritize.",
    )
    neighborhood_avoid: List[str] = Field(
        default_factory=list,
        description="Neighborhoods or areas to avoid.",
    )

    room_configuration: Optional[str] = Field(
        default=None,
        description="High‑level room configuration (e.g. '2 adults + 2 children in 1 suite').",
    )
    special_requirements: List[str] = Field(
        default_factory=list,
        description="Special requirements (e.g. step‑free access, crib, quiet room).",
    )

    prompt: Optional[str] = Field(
        default=None,
        description="Natural‑language description used by a search‑focused agent.",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Short machine‑readable label for this search (e.g. 'accommodation_options_lookup').",
    )


class AccommodationSearchResult(BaseModel):
    """
    Normalized result of an accommodation search for one AccommodationSearchTask.
    """

    task_id: str = Field(
        ...,
        description="ID of the AccommodationSearchTask this result corresponds to.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Final query string sent to the search tool or external API.",
    )

    options: List[AccommodationOption] = Field(
        default_factory=list,
        description="Structured candidate options (cheapest / best_location / family_friendly / balanced / luxury).",
    )

    summary: Optional[str] = Field(
        default=None,
        description="Concise summary of typical locations, property types, and price bands.",
    )
    best_price_hint: Optional[str] = Field(
        default=None,
        description="Typical lowest reasonable price range for the party.",
    )
    best_location_hint: Optional[str] = Field(
        default=None,
        description="Hint about the best located options (e.g. closest to key attractions).",
    )
    family_friendly_hint: Optional[str] = Field(
        default=None,
        description="Hint about family‑friendly or accessibility‑friendly options.",
    )
    neighborhood_hint: Optional[str] = Field(
        default=None,
        description="Guidance on which neighborhoods suit the travelers' preferences.",
    )

    recommended_option_label: Optional[str] = Field(
        default=None,
        description="Short label describing the recommended 'balanced' option.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes or caveats relevant for these accommodations.",
    )

    chosen_option_type: Optional[
        Literal["cheapest", "best_location", "family_friendly", "balanced", "luxury"]
    ] = Field(
        default=None,
        description="Which canonical option type was ultimately selected for this task, if any.",
    )
    selection_reason: Optional[str] = Field(
        default=None,
        description="Short explanation of why the chosen option type was selected.",
    )


class TravelerAccommodationChoice(BaseModel):
    """
    Per‑traveler view of a chosen accommodation option (plus alternatives) derived
    from AccommodationSearchResult entries.
    """

    traveler_index: int = Field(
        ...,
        description="Index into PlannerState.demographics.travelers.",
    )
    task_id: str = Field(
        ...,
        description="ID of the AccommodationSearchTask this choice came from.",
    )

    summary: Optional[str] = Field(
        default=None,
        description="Summary of locations/property types/prices relevant to this traveler or group.",
    )
    best_price_hint: Optional[str] = Field(
        default=None,
        description="Price hint scoped to this task.",
    )
    best_location_hint: Optional[str] = Field(
        default=None,
        description="Location hint scoped to this task.",
    )
    family_friendly_hint: Optional[str] = Field(
        default=None,
        description="Family‑friendly or accessibility hints scoped to this task.",
    )
    neighborhood_hint: Optional[str] = Field(
        default=None,
        description="Neighborhood guidance scoped to this task.",
    )

    recommended_option_label: Optional[str] = Field(
        default=None,
        description="Label for the recommended option for this traveler or group.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional caveats or remarks for this traveler's accommodations.",
    )

    chosen_option_type: Optional[
        Literal["cheapest", "best_location", "family_friendly", "balanced", "luxury"]
    ] = Field(
        default=None,
        description="Which canonical option type was ultimately selected.",
    )
    selection_reason: Optional[str] = Field(
        default=None,
        description="Short explanation of why the chosen option type was selected.",
    )

    chosen_option: Optional[AccommodationOption] = Field(
        default=None,
        description="The selected AccommodationOption for this traveler/group (if any).",
    )
    other_options: List[AccommodationOption] = Field(
        default_factory=list,
        description="Alternative AccommodationOptions that were considered but not selected.",
    )


class AccommodationState(BaseModel):
    """
    Container for all accommodation planning outputs for a given session.
    """

    search_tasks: List[AccommodationSearchTask] = Field(
        default_factory=list,
        description="Pending or completed accommodation search tasks to be run by a search agent.",
    )
    search_results: List[AccommodationSearchResult] = Field(
        default_factory=list,
        description="Normalized results from the accommodation search agent for each task.",
    )
    overall_summary: Optional[str] = Field(
        default=None,
        description="High‑level summary of accommodation options and cost implications for the whole party.",
    )
    traveler_accommodations: List[TravelerAccommodationChoice] = Field(
        default_factory=list,
        description="Per‑traveler or per‑group view of chosen accommodations and alternatives.",
    )

