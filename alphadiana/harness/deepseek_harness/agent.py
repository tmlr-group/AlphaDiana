"""DeepSeek SDK in a disposable Podman task container."""

from alphadiana.harness._internal.podman_agent import NativePodmanAgent
from alphadiana.harness.registry import AgentRegistry


class DeepSeekHarnessAgent(NativePodmanAgent):
    name = "deepseek_harness"


AgentRegistry.register("deepseek_harness", DeepSeekHarnessAgent)
