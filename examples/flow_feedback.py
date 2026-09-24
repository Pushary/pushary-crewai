"""Pause a CrewAI Flow for a phone decision, then resume it in a new process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Mapping

from crewai.flow import (
    Flow,
    HumanFeedbackPending,
    HumanFeedbackProvider,
    HumanFeedbackResult,
    PendingFeedbackContext,
    human_feedback,
    listen,
    start,
)
from pushary import PusharyServer


def pushary_client() -> PusharyServer:
    key = os.environ.get("PUSHARY_API_KEY")
    if not key:
        raise RuntimeError("Set PUSHARY_API_KEY before starting or resuming a review")
    return PusharyServer(key)


def recipient_id() -> str:
    recipient = os.environ.get("PUSHARY_EXTERNAL_ID", "").strip()
    if not recipient:
        raise RuntimeError("Set PUSHARY_EXTERNAL_ID to the enrolled customer's ID")
    return recipient


def review_question(context: PendingFeedbackContext) -> str:
    if not isinstance(context.method_output, str):
        raise ValueError("This example reviews a text proposal")
    question = f"{context.message}\n\n{context.method_output}"
    if len(question) > 500:
        raise ValueError("The review question exceeds Pushary's 500-character limit")
    return question


def review_context(context: PendingFeedbackContext, recipient: str) -> str:
    recipient_hash = hashlib.sha256(recipient.encode("utf-8")).hexdigest()
    return f"crewai:{context.flow_id}:{context.method_name}:{recipient_hash}"


class PusharyPhoneProvider(HumanFeedbackProvider):
    def request_feedback(self, context: PendingFeedbackContext, flow: Flow) -> str:
        recipient = recipient_id()
        created = pushary_client().decisions.create(
            review_question(context),
            type="confirm",
            external_id=recipient,
            context=review_context(context, recipient),
            idempotency_key=f"crewai:{context.flow_id}:{context.method_name}",
            require_reachable=True,
            expires_in_seconds=3600,
            wait=False,
        )
        decision_id = created.get("decisionId")
        if not isinstance(decision_id, str) or not decision_id:
            raise ValueError("Pushary did not return a decision ID")
        raise HumanFeedbackPending(context, callback_info={"decision_id": decision_id})


class ReviewFlow(Flow):
    @start()
    @human_feedback(message="Approve this proposed change?", provider=PusharyPhoneProvider())
    def propose(self) -> str:
        return self.state["proposal"]

    @listen(propose)
    def finish(self, result: HumanFeedbackResult) -> dict[str, str | bool]:
        if not isinstance(result.output, str):
            raise ValueError("The saved proposal is not text")
        return {"approved": result.feedback == "yes", "proposal": result.output}


def verified_feedback(
    decision: Mapping[str, object],
    context: PendingFeedbackContext,
    decision_id: str,
    recipient: str,
) -> str | None:
    if (
        decision.get("decisionId") != decision_id
        or decision.get("context") != review_context(context, recipient)
        or decision.get("question") != review_question(context)
        or decision.get("type") != "confirm"
        or decision.get("externalId") not in (None, recipient)
    ):
        raise ValueError("Decision does not match the pending flow and recipient")
    status = decision.get("status")
    if status == "pending":
        return None
    if status == "answered" and decision.get("answered") is True:
        value = decision.get("value")
        if value in ("yes", "no"):
            return value
    if status in ("expired", "cancelled"):
        return "no"
    raise ValueError("Decision has an unsupported or incomplete state")


def saved_resume(flow_id: str, decision_id: str, database: Path) -> dict[str, object] | None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT decision_id, status, output FROM resumed_flows WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
    if row is None:
        return None
    if row[0] != decision_id:
        raise ValueError("This flow was resumed with a different decision")
    if row[1] == "complete":
        if not isinstance(row[2], str):
            raise ValueError("Completed review has no saved result")
        result = json.loads(row[2])
        if not isinstance(result, dict):
            raise ValueError("Saved review result is invalid")
        return {"status": "complete", "result": result}
    return {"status": row[1]}


def claim_resume(flow_id: str, decision_id: str, database: Path) -> bool:
    with sqlite3.connect(database) as connection:
        return bool(
            connection.execute(
                "INSERT OR IGNORE INTO resumed_flows (flow_id, decision_id, status) VALUES (?, ?, 'resuming')",
                (flow_id, decision_id),
            ).rowcount
        )


def record_resume(flow_id: str, database: Path, status: str, output: object = None) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE resumed_flows SET status = ?, output = ? WHERE flow_id = ?",
            (status, json.dumps(output) if output is not None else None, flow_id),
        )


def start_review(proposal: str) -> dict[str, str]:
    if not proposal.strip() or len(proposal) > 300:
        raise ValueError("Give a nonempty proposal of at most 300 characters")
    pending = ReviewFlow().kickoff(inputs={"proposal": proposal})
    if not isinstance(pending, HumanFeedbackPending):
        raise RuntimeError("CrewAI did not pause for feedback")
    callback_info = pending.callback_info or {}
    decision_id = callback_info.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id:
        raise RuntimeError("CrewAI did not preserve the Pushary decision ID")
    return {
        "flow_id": pending.context.flow_id,
        "decision_id": decision_id,
    }


def resume_review(flow_id: str, decision_id: str, database: Path) -> dict[str, object]:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS resumed_flows "
            "(flow_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, status TEXT NOT NULL, output TEXT)"
        )
    previous = saved_resume(flow_id, decision_id, database)
    if previous is not None:
        return previous
    flow = ReviewFlow.from_pending(flow_id)
    context = flow.pending_feedback
    if context is None or context.flow_id != flow_id or context.method_name != "propose":
        raise ValueError("Flow is not waiting for this review")
    decision = pushary_client().decisions.get(decision_id)
    feedback = verified_feedback(decision, context, decision_id, recipient_id())
    if feedback is None:
        return {"status": "pending"}
    if not claim_resume(flow_id, decision_id, database):
        return saved_resume(flow_id, decision_id, database) or {"status": "uncertain"}
    try:
        output = flow.resume(feedback)
    except BaseException:
        record_resume(flow_id, database, "uncertain")
        raise
    record_resume(flow_id, database, "complete", output)
    return {"status": "complete", "result": output}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start").add_argument("proposal")
    resume = commands.add_parser("resume")
    resume.add_argument("flow_id")
    resume.add_argument("decision_id")
    resume.add_argument("--database", type=Path, default=Path("pushary-crewai-resumes.sqlite"))
    args = parser.parse_args()
    result = (
        start_review(args.proposal)
        if args.command == "start"
        else resume_review(args.flow_id, args.decision_id, args.database)
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
