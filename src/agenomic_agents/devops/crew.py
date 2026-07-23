import json
import os
from pathlib import Path
from typing import Any

from agenomic_agents.devops.guards import ToolBudget
from agenomic_agents.devops.models import IncidentAssessment, IncidentRequest

# CrewAI resolves optional runtime storage at import time. Keep its RAG storage
# inside the application's data root rather than the global user data directory.
os.environ.setdefault("CREWAI_STORAGE_DIR", str((Path("data") / "crewai").resolve()))


def build_incident_crew(incident: IncidentRequest, model: str, maximum_tool_calls: int) -> Any:
    # Lazy import avoids CrewAI initializing user-level optional services during
    # health checks and requests targeting the other agent frameworks.
    from crewai import LLM, Agent, Crew, Process, Task
    from crewai.tools import tool

    budget = ToolBudget(maximum=maximum_tool_calls)

    @tool("read_incident_logs")
    def read_incident_logs(query: str) -> str:
        """Search the bounded incident log snapshot for a case-insensitive query."""
        budget.record("read_incident_logs")
        needle = query.casefold()
        matches = [line for line in incident.logs if needle in line.casefold()][:50]
        return json.dumps(matches)

    @tool("read_incident_metrics")
    def read_incident_metrics(metric_name: str) -> str:
        """Read metrics from the immutable incident snapshot."""
        budget.record("read_incident_metrics")
        matches = [
            metric.model_dump()
            for metric in incident.metrics
            if metric_name.casefold() in metric.name.casefold()
        ]
        return json.dumps(matches[:50])

    llm = LLM(model=model, temperature=0)

    def make_agent(role: str, goal: str, tools: list[Any]) -> Agent:
        return Agent(
            role=role,
            goal=goal,
            backstory=(
                "A production operations specialist working under strict least-privilege rules."
            ),
            llm=llm,
            tools=tools,
            allow_delegation=False,
            max_iter=4,
            max_retry_limit=2,
            verbose=False,
        )

    log_reader = make_agent(
        "Log Reader",
        "Find concrete error evidence without inventing log lines.",
        [read_incident_logs],
    )
    metrics_reader = make_agent(
        "Metrics Analyst",
        "Identify anomalous metrics and timestamps without extrapolating absent data.",
        [read_incident_metrics],
    )
    root_cause = make_agent(
        "Root Cause Analyst",
        "Correlate evidence, state uncertainty, and avoid unsupported causal claims.",
        [],
    )
    remediation = make_agent(
        "Safe Remediation Planner",
        "Propose reversible actions only. Never execute commands or claim an action ran.",
        [],
    )
    notifier = make_agent(
        "Incident Communicator",
        "Produce an accurate notification and ticket summary with impact and next steps.",
        [],
    )

    tasks = [
        Task(
            description=(
                f"Analyze logs for incident {incident.incident_id}. Alert: {incident.alert}. "
                "Use the log tool and return only evidence present in the snapshot."
            ),
            expected_output="A concise list of relevant log evidence and uncertainties.",
            agent=log_reader,
        ),
        Task(
            description="Inspect the provided metrics for anomalies related to the alert.",
            expected_output="A concise list of metric evidence with names, values, and units.",
            agent=metrics_reader,
        ),
        Task(
            description="Correlate all prior evidence and determine the most likely root cause.",
            expected_output="Root cause hypothesis, confidence, severity, and supporting evidence.",
            agent=root_cause,
        ),
        Task(
            description=(
                "Create a safe remediation plan. Commands are proposals only and will be checked "
                "by a deterministic protection layer before any human considers them."
            ),
            expected_output="Ordered reversible actions, rollback notes, and proposed commands.",
            agent=remediation,
        ),
        Task(
            description=(
                "Create the final structured incident assessment and communication. Notification "
                "is mandatory. Do not say that remediation was executed."
            ),
            expected_output="A complete IncidentAssessment object.",
            output_pydantic=IncidentAssessment,
            agent=notifier,
        ),
    ]
    return Crew(
        agents=[log_reader, metrics_reader, root_cause, remediation, notifier],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
        memory=False,
        cache=False,
    )
