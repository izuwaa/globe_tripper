from typing import List, Optional

from pydantic import BaseModel, Field


class VisaRequirement(BaseModel):
    """
    Structured representation of visa requirements for a single traveler
    on a specific route (origin -> destination).
    """

    traveler_index: int = Field(
        ..., description="Index into PlannerState.demographics.travelers for this traveler."
    )
    origin: Optional[str] = Field(
        default=None,
        description="Departure location for this traveler (city/country).",
    )
    destination: Optional[str] = Field(
        default=None,
        description="Trip destination (city/country) for this traveler.",
    )
    nationality: Optional[str] = Field(
        default=None,
        description="Traveler's nationality relevant for visa determination.",
    )

    needs_visa: Optional[bool] = Field(
        default=None,
        description="Whether this traveler needs a visa for this trip.",
    )
    visa_type: Optional[str] = Field(
        default=None,
        description="Type of visa recommended (e.g. tourist, family visit).",
    )
    processing_time: Optional[str] = Field(
        default=None,
        description="Typical processing time (e.g. '15 working days').",
    )
    cost: Optional[str] = Field(
        default=None,
        description="Approximate cost or fee range for the visa.",
    )
    validity: Optional[str] = Field(
        default=None,
        description="Typical validity period (e.g. '6 months multiple entry').",
    )
    entry_conditions: Optional[List[str]] = Field(
        default=None,
        description="Key conditions or restrictions for entry.",
    )

    documents_required: Optional[List[str]] = Field(
        default=None,
        description="List of key documents required for the application.",
    )
    where_to_apply: Optional[str] = Field(
        default=None,
        description="High‑level guidance on where/how to apply (e.g. embassy, TLS/VFS center, online).",
    )
    appointment_requirements: Optional[str] = Field(
        default=None,
        description="Notes on biometrics, in‑person appointments, or interview.",
    )

    additional_notes: Optional[str] = Field(
        default=None,
        description="Free‑form notes or caveats the agent wants to highlight.",
    )


class VisaSearchTask(BaseModel):
    """
    Structured description of a single visa search to perform.
    Typically groups one or more travelers who share nationality/destination.
    """

    task_id: str = Field(
        ...,
        description="Unique identifier for this search task within the session.",
    )
    traveler_indexes: List[int] = Field(
        default_factory=list,
        description="Indexes into PlannerState.demographics.travelers that this task covers.",
    )
    origin_country: Optional[str] = Field(
        default=None,
        description="Country of departure for these travelers.",
    )
    destination_country: Optional[str] = Field(
        default=None,
        description="Destination country for these travelers.",
    )
    nationality: Optional[str] = Field(
        default=None,
        description="Nationality shared by this group of travelers.",
    )
    travel_purpose: Optional[str] = Field(
        default="tourism",
        description="High‑level purpose of travel (e.g. tourism, business).",
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Templated, human‑readable prompt describing what to search for this task.",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Short machine‑readable purpose label for this search (e.g. 'visa_requirements_lookup').",
    )


class VisaSearchResult(BaseModel):
    """
    Normalized result of a visa search for one VisaSearchTask.
    """

    task_id: str = Field(
        ...,
        description="ID of the VisaSearchTask this result corresponds to.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Final query string that was sent to the search tool.",
    )
    jurisdiction: Optional[str] = Field(
        default=None,
        description="Country/region focus of the search.",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Concise natural‑language summary of the findings.",
    )
    sources: Optional[List[str]] = Field(
        default=None,
        description="Human‑readable list of key official sources consulted.",
    )
    processing_time_hint: Optional[str] = Field(
        default=None,
        description="Typical processing time extracted from search results.",
    )
    fee_hint: Optional[str] = Field(
        default=None,
        description="Typical fee / cost extracted from search results.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes or caveats from the search agent.",
    )


class VisaState(BaseModel):
    """
    Container for all visa planning outputs for a given session.
    """

    requirements: List[VisaRequirement] = Field(
        default_factory=list,
        description="Per‑traveler visa requirements.",
    )
    overall_summary: Optional[str] = Field(
        default=None,
        description="High‑level summary of visa implications for the whole party.",
    )
    search_tasks: List[VisaSearchTask] = Field(
        default_factory=list,
        description="Pending or completed visa search tasks to be run by the search agent.",
    )
    search_results: List[VisaSearchResult] = Field(
        default_factory=list,
        description="Normalized results from the search agent for each search task.",
    )
    earliest_safe_departure_date: Optional[str] = Field(
        default=None,
        description=(
            "Recommended earliest departure date based on visa processing times, "
            "expressed as an ISO date computed from 'today' + a conservative "
            "processing buffer (days)."
        ),
    )
