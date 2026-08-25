"""Protocol metadata for the Pi/Terminal-Bench 2 evaluation bridge.

The optimizer lives outside AlphaDiana.  AlphaDiana records the protocol
variant that produced a result so reports cannot accidentally describe this
evaluation adapter as the original Meta-Harness implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_PHASES = frozenset({"standalone", "training", "final"})
_PARENT_POLICIES = frozenset({
    "overall_training_frontier", "population_proposer_selected", "external",
})


@dataclass(frozen=True)
class TerminalBench2EvaluationProtocol:
    """Immutable description of the evaluation-side adaptation contract."""

    name: str = "pi_meta_harness_adaptation"
    phase: str = "standalone"
    search_space: str = "pi_typescript_extension"
    parent_policy: str = "population_proposer_selected"
    candidates_per_iteration: int = 1
    proposer_workflow: str = "autonomous"
    validation_rewards_withheld: bool = True
    task_names_withheld: bool = False

    @classmethod
    def from_config(cls, value: Any) -> "TerminalBench2EvaluationProtocol":
        """Parse optional protocol metadata without changing runtime behavior."""
        if value in (None, ""):
            return cls()
        if not isinstance(value, dict):
            raise ValueError("agent.config.protocol must be a mapping")
        protocol = cls(
            name=str(value.get("name", cls.name) or cls.name),
            phase=str(value.get("phase", cls.phase) or cls.phase),
            search_space=str(value.get("search_space", cls.search_space) or cls.search_space),
            parent_policy=str(value.get("parent_policy", cls.parent_policy) or cls.parent_policy),
            candidates_per_iteration=int(
                value.get("candidates_per_iteration", cls.candidates_per_iteration)
            ),
            proposer_workflow=str(
                value.get("proposer_workflow", cls.proposer_workflow) or cls.proposer_workflow
            ),
            validation_rewards_withheld=bool(
                value.get("validation_rewards_withheld", cls.validation_rewards_withheld)
            ),
            task_names_withheld=bool(value.get("task_names_withheld", cls.task_names_withheld)),
        )
        protocol.validate()
        return protocol

    def validate(self) -> None:
        if self.phase not in _PHASES:
            raise ValueError(f"protocol.phase must be one of {sorted(_PHASES)}")
        if self.parent_policy not in _PARENT_POLICIES:
            raise ValueError(
                "protocol.parent_policy must be one of "
                f"{sorted(_PARENT_POLICIES)}"
            )
        if self.candidates_per_iteration < 1:
            raise ValueError("protocol.candidates_per_iteration must be >= 1")
        if self.phase == "final" and not self.validation_rewards_withheld:
            raise ValueError(
                "final evaluation must withhold validation rewards from the proposer"
            )
        if self.phase == "final" and not self.task_names_withheld:
            raise ValueError(
                "final evaluation must declare task_names_withheld=true; "
                "enforce the namespace boundary outside AlphaDiana"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "phase": self.phase,
            "search_space": self.search_space,
            "parent_policy": self.parent_policy,
            "candidates_per_iteration": self.candidates_per_iteration,
            "proposer_workflow": self.proposer_workflow,
            "validation_rewards_withheld": self.validation_rewards_withheld,
            "task_names_withheld": self.task_names_withheld,
            "original_meta_harness_claim": False,
        }
