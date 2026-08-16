"""Human-in-the-loop for CrewAI, powered by Pushary.

Replace CrewAI's console ``human_input=True`` prompt with a tool that reaches a real
person on their phone. ``make_ask_human_tool`` returns a ``BaseTool`` you hand to an
``Agent``; it blocks until the person answers and fails closed.

Everything but the CrewAI binding is the shared kernel from ``pushary.adapters``,
bound to this adapter's name.

Zero framework import at module load: CrewAI is imported lazily inside the tool
factory, so the core helpers work (and test) without it installed.
"""

from __future__ import annotations

from typing import Any, Optional

from pushary import SIGNATURE_HEADER, deterministic_key
from pushary.adapters import (
    AdapterKernel,
    ApprovalAsk,
    ApprovalDecision,
    describe_answer,
    is_affirmative,
    render_approval_question,
    resolve_pushary_callback,
)

__version__ = "0.2.0"

__all__ = [
    "connect",
    "ask_human",
    "make_ask_human_tool",
    "describe_answer",
    "resolve_pushary_callback",
    "is_affirmative",
    "render_approval_question",
    "deterministic_key",
    "create_pushary_gate",
    "require_pushary_external_id",
    "ApprovalAsk",
    "ApprovalDecision",
    "SIGNATURE_HEADER",
    "__version__",
]

_DEFAULT_DESCRIPTION = (
    "Ask a real human to approve, choose, or answer. Blocks until they reply on their phone."
)

_kernel = AdapterKernel("the CrewAI helpers")

connect = _kernel.connect
ask_human = _kernel.ask_human

#: Build a request-time approval gate bound to these helpers.
create_pushary_gate = _kernel.create_gate

#: The end-user to ask, or a clear error naming these helpers.
require_pushary_external_id = _kernel.require_external_id


def make_ask_human_tool(
    external_id: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    agent_name: Optional[str] = None,
    name: str = "ask_human",
    description: Optional[str] = None,
    node: str = "ask-human",
):
    """Return a CrewAI ``BaseTool`` that asks ``external_id`` and blocks on their answer.

    ``external_id`` is bound here, never taken from the model, so a prompt-injected
    agent cannot redirect an approval to another user. Give the returned tool to an
    ``Agent(tools=[...])`` and drop ``human_input=True``.

    ```python
    agent = Agent(role="Ops", goal="Ship safely", tools=[make_ask_human_tool("user_123")])
    ```
    """

    # Lazy import so the module loads (and tests) without CrewAI installed.
    from typing import Type

    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field

    tool_name = name or "ask_human"
    tool_description = description or _DEFAULT_DESCRIPTION

    class _AskHumanInput(BaseModel):
        question: str = Field(..., description="The exact question to put to the human.")
        type: str = Field(
            "confirm",
            description="confirm = yes/no, select = pick an option, input = free text.",
        )

    class AskHumanTool(BaseTool):
        name: str = tool_name
        description: str = tool_description
        args_schema: Type[BaseModel] = _AskHumanInput

        def _run(self, question: str, type: str = "confirm", **_: Any) -> str:
            result = ask_human(
                question,
                external_id=external_id,
                type=type,
                node=node,
                agent_name=agent_name,
                api_key=api_key,
                base_url=base_url,
            )
            return describe_answer(type, result)

    return AskHumanTool()
