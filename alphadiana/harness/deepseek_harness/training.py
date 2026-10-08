"""DeepSeek wire fields at the shared native-token capture boundary."""

from collections.abc import Mapping

from alphadiana.harness._internal.training import NativeCapture


class DeepSeekCapture(NativeCapture):
    def prepare(self, request):
        if not isinstance(request, Mapping):
            raise ValueError("provider request must be an object")
        # Disabled reasoning must originate in the real runtime. Never silently
        # discard an enabled reasoning request or rewrite model messages.
        if request.get("thinking") != {"type": "disabled"} or "reasoning_effort" in request:
            raise ValueError("DeepSeek training requires reasoning disabled by the runtime")
        if self.plan.chat_template_kwargs.get("enable_thinking") is not False:
            raise ValueError("DeepSeek training requires the non-thinking chat template")
        inventory = request.get("dsh_plugin_packages")
        if inventory is not None and (
            not isinstance(inventory, Mapping)
            or set(inventory) != {"version", "packages"}
            or type(inventory["version"]) is not int
            or inventory["version"] != 1
            or not isinstance(inventory["packages"], list)
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"name", "version"}
                or any(not isinstance(v, str) or not v for v in item.values())
                for item in inventory["packages"]
            )
        ):
            raise ValueError("Unsupported DeepSeek plugin inventory")
        # The pinned SDK declares this inventory as model-hidden provider
        # metadata. The model sees the unchanged messages/tools; raw harness
        # requests are retained by TokenCaptureEndpoint for audit.
        payload = dict(request)
        payload.pop("thinking")
        payload.pop("dsh_plugin_packages", None)
        return super().prepare(payload)
