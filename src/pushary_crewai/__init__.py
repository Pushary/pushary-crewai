"""Human-in-the-loop for CrewAI, powered by Pushary.

Replace CrewAI's console ``human_input=True`` prompt with a tool that reaches a real
person on their phone. ``make_ask_human_tool`` returns a ``BaseTool`` you hand to an
``Agent``; it blocks until the person answers and fails closed.

Zero framework import at module load: CrewAI is imported lazily inside the tool
factory, so the core helpers work (and test) without it installed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from pushary import (
    PusharyServer,
    SIGNATURE_HEADER,
    deterministic_key,
    is_approved,
    parse_decision_callback,
    verify_webhook_signature,
)

__version__ = "0.1.0"

__all__ = [
    "connect",
    "ask_human",
    "make_ask_human_tool",
    "describe_answer",
    "resolve_pushary_callback",
    "is_affirmative",
    "deterministic_key",
    "SIGNATURE_HEADER",
    "__version__",
]

_DEFAULT_DESCRIPTION = "Ask a real human to approve, choose, or answer. Blocks until they reply on their phone."


def _client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> PusharyServer:
    key = api_key or os.environ.get("PUSHARY_API_KEY")
    if not key:
        raise ValueError("Pushary: set PUSHARY_API_KEY or pass api_key=... to the CrewAI helpers.")
    return PusharyServer(api_key=key, base_url=base_url)


def _idempotency_key(external_id: str, node: str, question: str) -> str:
    return deterministic_key([external_id, node, question])


def is_affirmative(answer: Optional[str]) -> bool:
    """Fail-closed yes/no check for a confirm answer."""
    return is_approved("answered", "confirm", answer)


def connect(external_id: str, *, api_key: Optional[str] = None, base_url: Optional[str] = None) -> str:
    """Connect one end-user's phone (keyless). Returns a single-use link to show them."""
    return _client(api_key, base_url).enroll(external_id)["universalLink"]


def ask_human(
    question: str,
    *,
    external_id: str,
    type: str = "confirm",
    options: Optional[List[str]] = None,
    node: str = "ask-human",
    context: Optional[str] = None,
    agent_name: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Blocking ask: create a decision and poll durably until answered or the deadline.

    Returns the decision dict with a fail-closed ``approved`` flag. Idempotency is
    keyed by external_id + node + question.
    """
    return _client(api_key, base_url).decisions.ask(
        question,
        type=type,
        options=options,
        external_id=external_id,
        context=context,
        agent_name=agent_name,
        timeout_seconds=timeout_seconds,
        idempotency_key=_idempotency_key(external_id, node, question),
    )


def describe_answer(type: str, result: Dict[str, Any]) -> str:
    """Turn a decision outcome into an unambiguous instruction for the agent."""
    if not result.get("answered"):
        return (
            f"No answer (status: {result.get('status')}). "
            "Treat this as NOT approved and do not proceed."
        )
    if type == "confirm":
        return (
            "The human approved. You may proceed."
            if result.get("approved")
            else "The human declined. Do not proceed."
        )
    return f"The human answered: {result.get('value') or ''}"


def resolve_pushary_callback(
    raw_body: Any, signature: Optional[str], secret: str
) -> Optional[Dict[str, Any]]:
    """Verify a callback signature and parse it, or return None."""
    if not verify_webhook_signature(raw_body, signature, secret):
        return None
    cb = parse_decision_callback(raw_body)
    if not cb:
        return None
    return {
        "correlationId": cb.get("correlationId"),
        "answer": cb.get("answer"),
        "value": cb.get("value"),
        "approved": is_affirmative(cb.get("answer")),
        "context": cb.get("context"),
        "answeredAt": cb.get("answeredAt"),
    }


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
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field
    from typing import Type

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
