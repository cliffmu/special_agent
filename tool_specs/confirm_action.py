"""Ask the user to confirm before executing an action."""
from __future__ import annotations
import logging
import voluptuous as vol
from typing import Iterable, Dict, Any

from ..agent_core import ToolSpec
from .prompt_user import prompt_user
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("action"):   str,
        vol.Required("targets"):  vol.Any(str, [str]),
        vol.Required("question"): str,              # ★ NEW – crafted by the LLM
    }
)

async def confirm_action(action: str,
                         targets: Iterable[str] | str,
                         question: str,
                         hass=None) -> Dict[str, Any]:
    if isinstance(targets, str):
        targets = [targets]
    log.debug("confirm_action: %s", question)
    # Delegate to unified plumbing
    return await prompt_user(
        prompt=question,
        kind="confirm",
        pending={"action": action, "targets": list(targets)},
        hass=hass,
    )

SPEC = ToolSpec(
    name="confirm_action",
    description="Ask the user for confirmation before a control action.",
    parameters=PARAMS,
    returns="dict(speak, kind='confirm', pending)",
    func=confirm_action,
)


# """Ask the user to confirm a planned action."""

# from __future__ import annotations

# import logging
# from typing import List

# import voluptuous as vol

# from ..agent_core import ToolSpec
# from ..utils import logging as log

# _LOGGER = logging.getLogger(__package__)

# PARAMS = vol.Schema(
#     {
#         vol.Required("action"): str,
#         vol.Required("targets"): [str],
#     }
# )


# async def confirm_action(action: str, targets: List[str], hass=None) -> str:
#     """Return a formatted confirmation question."""
#     target_str = ", ".join(targets)
#     question = f"Do you want to {action} {target_str}?"
#     log.debug("confirm_action: %s", question)
#     return question


# SPEC = ToolSpec(
#     name="confirm_action",
#     description="Generate a confirmation question for the user.",
#     parameters=PARAMS,
#     returns="question string",
#     func=confirm_action,
# )
