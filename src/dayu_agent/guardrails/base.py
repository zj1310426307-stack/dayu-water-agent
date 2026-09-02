"""Small guardrail contracts that remain independent of any provider SDK."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from dayu_agent.runtime.context import AgentContext
from dayu_agent.tools.base import ToolDefinition, ToolRiskLevel
from dayu_agent.tools.permissions import ToolPermission


class GuardrailDecision(BaseModel):
    """Normalized allow/block result with a client-safe reason."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str | None = None


class InputGuardrail(ABC):
    """Contract for checks that run before session or provider mutation."""

    @abstractmethod
    async def evaluate(self, content: str, context: AgentContext) -> GuardrailDecision:
        """Evaluate user input without modifying runtime state."""


class OutputGuardrail(ABC):
    """Contract for checks that run before assistant output is persisted."""

    @abstractmethod
    async def evaluate(self, content: str, context: AgentContext) -> GuardrailDecision:
        """Evaluate normalized provider or tool output."""


class ToolGuardrail(ABC):
    """Contract for checks that run before the Tool Registry executes."""

    @abstractmethod
    async def evaluate(
        self,
        tool: ToolDefinition,
        context: AgentContext,
    ) -> GuardrailDecision:
        """Evaluate risk policy without invoking the handler."""


class NonEmptyInputGuardrail(InputGuardrail):
    """Reject empty or whitespace-only messages."""

    async def evaluate(self, content: str, context: AgentContext) -> GuardrailDecision:
        """Allow only meaningful text; context is accepted for future policies."""

        del context
        if not content.strip():
            return GuardrailDecision(allowed=False, reason="Message must not be empty.")
        return GuardrailDecision(allowed=True)


class NonEmptyOutputGuardrail(OutputGuardrail):
    """Prevent a provider failure from being reported as an empty success."""

    async def evaluate(self, content: str, context: AgentContext) -> GuardrailDecision:
        """Allow only non-empty normalized assistant output."""

        del context
        if not content.strip():
            return GuardrailDecision(allowed=False, reason="Provider output must not be empty.")
        return GuardrailDecision(allowed=True)


class PhaseZeroToolGuardrail(ToolGuardrail):
    """Deny dangerous tools and undeclared permissions in Phase-00."""

    async def evaluate(
        self,
        tool: ToolDefinition,
        context: AgentContext,
    ) -> GuardrailDecision:
        """Fail closed for dangerous policy values before registry execution."""

        del context
        if tool.permission is ToolPermission.DANGEROUS:
            return GuardrailDecision(allowed=False, reason="Dangerous permission is prohibited.")
        if tool.risk_level is ToolRiskLevel.DANGEROUS:
            return GuardrailDecision(allowed=False, reason="Dangerous risk is prohibited.")
        return GuardrailDecision(allowed=True)
