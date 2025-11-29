from typing import Dict, Any

from google.adk.tools.tool_context import ToolContext

from src.state.planner_state import PlannerState
from src.state.state_utils import get_planner_state, save_planner_state, is_intake_complete


def mark_ready_for_planning(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Explicitly transition PlannerState.status from \"intake\" to \"planning\"
    once the LLM has gathered all critical information and the user has
    confirmed they are ready to move into the planning phase.

    This does not itself gather any new information; it simply checks that
    `is_intake_complete` returns True for the current PlannerState and, if so,
    flips the status to \"planning\".
    """
    state: PlannerState = get_planner_state(tool_context)

    if state.status != "intake":
        # No-op if we've already moved past intake.
        return {
            "status": "skipped",
            "reason": "not_in_intake",
            "current_status": state.status,
        }

    if not is_intake_complete(state):
        return {
            "status": "error",
            "reason": "intake_incomplete",
        }

    state.status = "planning"
    save_planner_state(tool_context, state)

    return {
        "status": "success",
        "new_status": state.status,
    }

