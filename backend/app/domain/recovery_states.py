from __future__ import annotations

ACTIVE_RECOVERY_CASE_STATUSES = ("NEW", "ASSESSING")
TERMINAL_RECOVERY_CASE_STATUSES = ("CLOSED",)


def advance_recovery_case_status(current: str, incoming: str) -> str:
    transitions = {"NEW": {"ASSESSING", "CLOSED"}, "ASSESSING": {"CLOSED"}, "CLOSED": set()}
    if current == incoming:
        return current
    if incoming not in transitions.get(current, set()):
        raise ValueError(f"unsupported recovery case transition: {current} -> {incoming}")
    return incoming
