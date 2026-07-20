"""Minimal CrewAI example: an agent with an ask-human tool that reaches a phone.

Prereqs: pip install pushary-crewai crewai
Run:     PUSHARY_API_KEY=... OPENAI_API_KEY=... python examples/basic.py
"""
from crewai import Agent, Crew, Task
from pushary_crewai import connect, make_ask_human_tool

USER_ID = "user_123"


def main() -> None:
    # 1) One time per end-user: connect their phone.
    link = connect(USER_ID)
    print("Ask the user to open:", link)

    # 2) Bind an ask-human tool to that user and give it to an agent.
    agent = Agent(
        role="Ops",
        goal="Ship safely",
        backstory="Careful operator.",
        tools=[make_ask_human_tool(USER_ID)],
    )
    task = Task(
        description="Draft the release. Before finalizing, call ask_human to get approval.",
        expected_output="approved release notes",
        agent=agent,
    )
    Crew(agents=[agent], tasks=[task]).kickoff()


if __name__ == "__main__":
    main()
