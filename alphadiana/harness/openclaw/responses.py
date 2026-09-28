"""Pinned OpenClaw Responses transport for image-bearing native agent turns.

The 2026.3.2 chat endpoint discards image blocks. Its Responses endpoint uses
OpenResponses source objects, not OpenAI's image_url input shape.
"""

from copy import deepcopy


def has_images(messages: list[dict]) -> bool:
    return any(
        isinstance(message.get("content"), list)
        and any(part.get("type") == "image_url" for part in message["content"])
        for message in messages
    )


def build_responses_payload(chat: dict) -> dict:
    items = []
    for message in chat["messages"]:
        content = message["content"]
        if isinstance(content, list):
            parts = []
            for part in content:
                if part["type"] == "text":
                    parts.append({"type": "input_text", "text": part["text"]})
                elif part["type"] == "image_url":
                    url = part["image_url"]["url"]
                    if url.startswith("data:"):
                        header, data = url.split(",", 1)
                        if not header.endswith(";base64"):
                            raise ValueError("OpenClaw images require base64 data URLs")
                        source = {
                            "type": "base64",
                            "media_type": header[5:-7],
                            "data": data,
                        }
                    else:
                        source = {"type": "url", "url": url}
                    parts.append({"type": "input_image", "source": source})
                else:
                    raise ValueError(f"Unsupported OpenClaw input part: {part['type']}")
            content = parts
        items.append({"type": "message", "role": message["role"], "content": content})
    payload = {
        key: deepcopy(chat[key])
        for key in ("model", "stream", "temperature", "top_p", "user")
        if key in chat
    }
    payload["input"] = items
    if "max_tokens" in chat:
        payload["max_output_tokens"] = chat["max_tokens"]
    # Provider-specific generation/capture settings belong to the existing
    # runtime model configuration and probability proxy, not this endpoint.
    return payload


def response_as_chat(resource: dict) -> dict:
    status = resource.get("status")
    if status != "completed":
        raise ValueError(f"OpenClaw Responses did not complete: {resource}")
    text = "\n\n".join(
        part["text"]
        for item in resource.get("output", [])
        if item.get("type") == "message" and item.get("role") == "assistant"
        for part in item.get("content", [])
        if part.get("type") == "output_text" and isinstance(part.get("text"), str)
    )
    result = {
        "id": resource.get("id"),
        "model": resource.get("model"),
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "native_response": deepcopy(resource),
    }
    usage = resource.get("usage")
    if usage:
        result["usage"] = {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    return result


def event_as_chat(event: dict) -> dict:
    kind = event.get("type")
    if kind == "response.output_text.delta":
        return {"choices": [{"delta": {"content": event["delta"]}}]}
    if kind in {"response.completed", "response.failed", "response.incomplete"}:
        result = response_as_chat(event["response"])
        # Completed output duplicates the deltas; retain it as native evidence.
        result["choices"][0]["delta"] = {}
        return result
    if kind == "error":
        raise ValueError(f"OpenClaw Responses error: {event}")
    return {}
