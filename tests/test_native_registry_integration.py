"""The shared loader must retain main's native agents after integration."""
import subprocess
import sys


def test_fresh_registry_loader_retains_pi_and_deepseek():
    subprocess.run(
        [sys.executable, "-c", "\n".join([
            "from alphadiana.registry_loader import load_builtin_components",
            "from alphadiana.harness.registry import AgentRegistry",
            "load_builtin_components()",
            "assert AgentRegistry.get('pi').name == 'pi'",
            "assert AgentRegistry.get('deepseek_harness').name == 'deepseek_harness'",
        ])],
        check=True, capture_output=True, text=True, timeout=30,
    )
