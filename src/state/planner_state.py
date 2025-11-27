from typing import Optional, List, Literal
from pydantic import BaseModel, Field


# Target Schema
class TripDetails(BaseModel):
    destination: Optional[str] = None
    origin: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    flexible_dates: Optional[bool] = False


class Traveler(BaseModel):
    role: Literal["adult", "child", "senior"]
    age: Optional[int] = None
    nationality: Optional[str] = None
    # Optional per-traveler origin if not shared with the main trip origin.
    origin: Optional[str] = None
    # Interests specific to this traveler (e.g. ["cars"], ["shopping"]).
    interests: Optional[List[str]] = None
    # Additional attributes like dietary restrictions, mobility needs, sensory needs.
    mobility_needs: Optional[List[str]] = None
    dietary_needs: Optional[List[str]] = None
    sensory_needs: Optional[List[str]] = None
    special_requirements: Optional[List[str]] = None


class Demographics(BaseModel):
    # Backwards-compatible aggregate counts.
    adults: Optional[int] = 1
    children: Optional[int] = 0
    seniors: Optional[int] = 0
    # Aggregate nationalities across the party (e.g. ["Nigerian", "American"])
    nationality: Optional[List[str]] = None
    # Optional per-traveler breakdown for more detailed logic (e.g. visas).
    travelers: List[Traveler] = []


class Preferences(BaseModel):
    budget_mode: Optional[Literal["economy", "standard", "luxury"]] = "standard"
    total_budget: Optional[float] = None
    pace: Optional[Literal["relaxed", "moderate", "busy"]] = "moderate"
    interests: Optional[List[str]] = []
    # e.g. ["vegetarian meals", "wheelchair access", "treated like royalty"]
    special_requests: Optional[List[str]] = []
    # Free‑form notes about preferences or constraints.
    notes: Optional[str] = None
    # Accommodation and location preferences.
    accommodation_preferences: Optional[List[str]] = None  # e.g. ["5-star hotels", "apartment stays"]
    room_configuration: Optional[str] = None  # e.g. "2 connecting rooms", "1 suite + 1 twin"
    neighborhood_preferences: Optional[List[str]] = None  # e.g. ["central", "quiet"]
    neighborhood_avoid: Optional[List[str]] = None  # e.g. ["party areas", "airport vicinity"]
    # Mobility / dietary / sensory constraints at the trip level.
    mobility_constraints: Optional[List[str]] = None
    dietary_requirements: Optional[List[str]] = None
    sensory_needs: Optional[List[str]] = None
    # Priority cues for planning.
    must_do: Optional[List[str]] = None
    nice_to_have: Optional[List[str]] = None
    # Internal transport preferences (e.g. ["train", "private car"]).
    transport_preferences: Optional[List[str]] = None
    # Arrival/departure logistics.
    airport_pickup_required: Optional[bool] = None
    luggage_count: Optional[int] = None
    # Daily rhythm / schedule notes (e.g. "kids nap 1–3pm", "no activities before 10am").
    daily_rhythm: Optional[str] = None


class PlannerState(BaseModel):
    trip_details: TripDetails = Field(default_factory=TripDetails)
    demographics: Demographics = Field(default_factory=Demographics)
    preferences: Preferences = Field(default_factory=Preferences)
    status: Literal["intake", "planning", "booked", "completed"] = "intake"
