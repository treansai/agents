import re
from collections import Counter

DANGEROUS_COMMAND_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brm\s+-rf\b",
        r"\bkubectl\s+delete\b",
        r"\bterraform\s+destroy\b",
        r"\bdrop\s+(database|table)\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bchmod\s+-R\s+777\b",
    )
]


class ToolBudget:
    def __init__(self, maximum: int, repeated_limit: int = 3) -> None:
        self.maximum = maximum
        self.repeated_limit = repeated_limit
        self.calls: Counter[str] = Counter()

    def record(self, tool_name: str) -> None:
        self.calls[tool_name] += 1
        total = sum(self.calls.values())
        if total > self.maximum:
            raise RuntimeError("Global tool-call budget exceeded; execution stopped")
        if self.calls[tool_name] > self.repeated_limit:
            raise RuntimeError(f"Loop detected for tool {tool_name}; execution stopped")


def blocked_commands(commands: list[str]) -> list[str]:
    return [
        command
        for command in commands
        if any(pattern.search(command) for pattern in DANGEROUS_COMMAND_PATTERNS)
    ]
