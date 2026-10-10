"""Pi JSON CLI in a disposable Podman task container."""

from alphadiana.harness._internal.podman_agent import NativePodmanAgent
from alphadiana.engine.components import AgentRegistry


class PiAgent(NativePodmanAgent):
    supports_training_capture = True
    name = "pi"


AgentRegistry.register("pi", PiAgent)
