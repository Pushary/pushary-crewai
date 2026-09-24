# pushary-crewai

[![CI](https://github.com/Pushary/pushary-crewai/actions/workflows/ci.yml/badge.svg)](https://github.com/Pushary/pushary-crewai/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pushary-crewai)](https://pypi.org/project/pushary-crewai/)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Full walkthrough: [Human-in-the-loop for CrewAI](https://pushary.com/human-in-the-loop-crewai?utm_source=github&utm_medium=oss-adapter&utm_campaign=pushary-crewai&utm_content=readme). Reaching your own end-users on their phones is the Pushary [Partner plan](https://pushary.com/human-in-the-loop?utm_source=github&utm_medium=oss-adapter&utm_campaign=pushary-crewai&utm_content=readme).

Human-in-the-loop for [CrewAI](https://www.crewai.com). Replace the console
`human_input=True` prompt with a tool that reaches a real person on their phone and
blocks until they answer, fail-closed.

Requires the Pushary [Partner plan](https://pushary.com/agent-notifications-integration?utm_source=github&utm_medium=oss-adapter&utm_campaign=pushary-crewai&utm_content=readme).

## Install

```bash
pip install pushary-crewai
```

Set `PUSHARY_API_KEY` (get it in your [dashboard](https://pushary.com/dashboard/settings)).

## Connect a phone once

```python
from pushary_crewai import connect

link = connect("user_123")  # show this to your end-user; one tap connects their phone
```

## Give an agent an ask-human tool

```python
from crewai import Agent, Task, Crew
from pushary_crewai import make_ask_human_tool

agent = Agent(
    role="Ops",
    goal="Ship safely",
    backstory="Careful operator.",
    tools=[make_ask_human_tool("user_123")],
)
task = Task(
    description="Draft the release. Before finalizing, call ask_human to get approval.",
    expected_output="approved release notes",
    agent=agent,
)
Crew(agents=[agent], tasks=[task]).kickoff()
```

The tool blocks until the person answers and returns a fail-closed instruction. The
`external_id` is bound when you build the tool, never taken from the model, so a
prompt-injected agent cannot ask the wrong person.

## Lower-level helpers

```python
from pushary_crewai import ask_human

d = ask_human("Approve this refund?", external_id="user_123", type="confirm")
if d["approved"]:
    issue_refund()
```

## API

- `connect(external_id, *, api_key=None, base_url=None)` — enroll an end-user's phone.
- `make_ask_human_tool(external_id, *, name=..., ...)` — a CrewAI `BaseTool` bound to that user.
- `ask_human(question, *, external_id, type="confirm", ...)` — blocking, returns the decision dict.
- `resolve_pushary_callback(raw_body, signature, secret)` — verify + parse a callback for a durable path.
- `describe_answer(type, result)`, `is_affirmative(answer)`, `deterministic_key(parts)`, `SIGNATURE_HEADER`.

## Example

The [basic example](examples/basic.py) gives an agent a blocking ask-human tool.

The [Flow example](examples/flow_feedback.py) uses CrewAI's native
`@human_feedback(provider=...)` path. It creates a Pushary decision, saves the
pending Flow, and exits. A later command reads the decision from Pushary and
resumes the same Flow. No model key or open worker is needed. Tested with
`crewai==1.15.22` and `pushary==2.1.1`.

```bash
python -m pip install -e . 'crewai==1.15.22'
export PUSHARY_API_KEY='your Partner server key'
export PUSHARY_EXTERNAL_ID='your enrolled customer ID'
export CREWAI_STORAGE_DIR="$(mktemp -d)"
python examples/flow_feedback.py start 'Send the release note'
# Answer on the connected phone. Copy flow_id and decision_id from the output.
python examples/flow_feedback.py resume FLOW_ID DECISION_ID \
  --database "$CREWAI_STORAGE_DIR/resumes.sqlite"
```

Keep `CREWAI_STORAGE_DIR` and the resume database across processes. A pending
decision returns `pending`; a decline or expiry returns `approved: false`.
The example checks the decision ID, exact question, Flow, and recipient before
resuming. Its SQLite claim prevents two local workers from resuming the same
Flow concurrently; a failed or interrupted resume stays claimed for manual
inspection. Use shared transactional storage across hosts.

The Flow only returns the review result. If you attach a business action,
bind its exact arguments and recipient in your application, and make the
action idempotent. Do not treat a model's approval claim or a direct call to
`flow.resume()` as authorization.

## License

MIT
