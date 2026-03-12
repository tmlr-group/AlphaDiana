"""Quick test for streaming mode against the OpenClaw gateway.

Usage:
    python test_stream.py --api-base <url> [--token <token>] [--model <model>]

Example (ROCK-proxied gateway):
    python test_stream.py \
        --api-base "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1" \
        --token OPENCLAW \
        --model openclaw

Example (OpenRouter):
    python test_stream.py \
        --api-base "https://openrouter.ai/api/v1" \
        --token "$OPENROUTER_API_KEY" \
        --model "qwen/qwen3-vl-235b-a22b-thinking"
"""
import argparse
import json
import sys
import time


def test_stream(api_base: str, token: str, model: str, prompt: str, timeout: float) -> bool:
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx not installed. Run: pip install httpx")
        sys.exit(1)

    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "stream": True,
    }

    print(f"URL    : {url}")
    print(f"Model  : {model}")
    print(f"Timeout: {timeout}s")
    print(f"Prompt : {prompt}")
    print("-" * 60)

    chunks: list[str] = []
    chunk_count = 0
    usage: dict = {}
    t0 = time.monotonic()
    first_chunk_time: float | None = None

    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                print(f"HTTP status: {response.status_code}")
                if response.status_code != 200:
                    body = response.read().decode()
                    print(f"ERROR body: {body}")
                    return False

                print("Streaming response:")
                print()

                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data)
                    except json.JSONDecodeError:
                        print(f"  [bad JSON chunk]: {data!r}")
                        continue

                    chunk_count += 1
                    choices = chunk_json.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    content = delta.get("content")
                    if content:
                        if first_chunk_time is None:
                            first_chunk_time = time.monotonic() - t0
                        chunks.append(content)
                        print(content, end="", flush=True)

                    if chunk_json.get("usage"):
                        usage = chunk_json["usage"]

    except httpx.TimeoutException as e:
        print(f"\n\nERROR: Request timed out after {timeout}s — {e}")
        return False
    except httpx.HTTPStatusError as e:
        print(f"\n\nERROR: HTTP error — {e}")
        return False
    except Exception as e:
        print(f"\n\nERROR: Unexpected error — {type(e).__name__}: {e}")
        return False

    total_time = time.monotonic() - t0
    raw_output = "".join(chunks)

    print()
    print()
    print("-" * 60)
    print(f"Chunks received  : {chunk_count}")
    print(f"Total chars      : {len(raw_output)}")
    print(f"Time to 1st chunk: {first_chunk_time:.2f}s" if first_chunk_time else "Time to 1st chunk: N/A (no content)")
    print(f"Total wall time  : {total_time:.2f}s")
    if usage:
        print(f"Token usage      : prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}")

    if not raw_output:
        print("\nFAIL: No content received")
        return False

    print("\nPASS: Streaming completed successfully")
    return True


def main():
    parser = argparse.ArgumentParser(description="Test streaming mode for OpenClaw / OpenRouter gateway")
    parser.add_argument("--api-base", required=True, help="Base URL of the gateway (without /chat/completions)")
    parser.add_argument("--token", default="OPENCLAW", help="Authorization token (default: OPENCLAW)")
    parser.add_argument("--model", default="openclaw", help="Model name (default: openclaw)")
    parser.add_argument("--timeout", type=float, default=600.0, help="Request timeout in seconds (default: 600)")
    parser.add_argument(
        "--prompt",
        default="Count from 1 to 20, one number per line.",
        help="Prompt to send (default: count from 1 to 20)",
    )
    args = parser.parse_args()

    ok = test_stream(
        api_base=args.api_base,
        token=args.token,
        model=args.model,
        prompt=args.prompt,
        timeout=args.timeout,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
