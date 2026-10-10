"""DeepSeek SDK in a disposable Podman task container."""

from alphadiana.harness._internal.podman_agent import NativePodmanAgent
from alphadiana.engine.components import AgentRegistry


class DeepSeekHarnessAgent(NativePodmanAgent):
    supports_training_capture = True
    name = "deepseek_harness"

    def _capture_class(self):
        from .training import DeepSeekCapture

        return DeepSeekCapture


AgentRegistry.register("deepseek_harness", DeepSeekHarnessAgent)
