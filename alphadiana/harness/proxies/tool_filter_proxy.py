"""
Tool-stripping HTTP proxy with optional system-prompt patching.

Filters /v1/chat/completions request body in two ways:
  1. tools=[...] — keep only entries whose function.name matches --allow patterns
  2. system message text — surgically strip sentences/blocks mentioning --strip-prompt-tokens

SSE response forwarded unchanged.

Usage:
  python tool_filter_proxy.py --port 9050 \\
    --upstream https://api.example.com/v1 \\
    --api-key sk-... --model qwen \\
    --allow "" --strip-prompt-tokens "bash,Bash,shell,python,Python"
"""
import argparse, json, re, sys, time
from aiohttp import web, ClientSession, ClientTimeout


def strip_prompt_mentions(text, tokens):
    """Remove sentences / bullet lines / md sections mentioning any token (case-insensitive)."""
    out = text
    for tok in tokens:
        # 1. delete markdown sections starting with "#" headers that mention tok
        out = re.sub(rf"(?ms)^#+\s+.*\b{re.escape(tok)}\b.*?(?=^#+\s|\Z)", "", out)
        # 2. delete sentences mentioning tok (split by . ! ? \n)
        out = re.sub(rf"[^.!?\n]*\b{re.escape(tok)}\b[^.!?\n]*[.!?]", "", out, flags=re.I)
        # 3. delete bullet lines mentioning tok
        out = re.sub(rf"(?m)^\s*[-*]\s.*\b{re.escape(tok)}\b.*$", "", out, flags=re.I)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


class Proxy:
    def __init__(self, upstream, api_key, allow_patterns, block_patterns, model_rewrite,
                 strip_prompt_tokens, strip_user_intro_tokens, harness_strip=None, ignore_providers=None,
                 only_providers=None):
        self.upstream = upstream.rstrip("/")
        self.api_key = api_key
        self.allow = [re.compile(p, re.I) for p in (allow_patterns or [])]
        self.block = [re.compile(p, re.I) for p in (block_patterns or [])]
        self.model_rewrite = model_rewrite
        self.strip_tokens = strip_prompt_tokens or []
        self.strip_user_intro_tokens = strip_user_intro_tokens or []
        self.harness_strip = harness_strip  # 'opencode' | 'openclaw' | 'zeroclaw' | None
        self.replace_system_prompt = None  # set externally; if not None, REPLACE entire system prompt
        self.reasoning_plain = False  # if True, merge reasoning into content WITHOUT tags (for OC AI SDK)
        self.reasoning_tag = "reasoning"  # tag name used to wrap reasoning in delta.content (default "reasoning" for ZC; OC AI SDK expects "think")
        self.inject_final_tool_call = False  # if True, when stream ends with finish_reason="stop", inject a synthetic tool_call chunk and change finish_reason to "tool_calls" — forces OC AI SDK to fire text-end on pure-text streams
        self.ignore_providers = ignore_providers or []  # OpenRouter provider routing
        self.only_providers = only_providers or []      # OpenRouter provider.only=[...]
        self.reasoning_override = None  # set externally before run
        self.rename_reasoning = False  # rename OR responses message.reasoning -> message.reasoning_content

    def filter_tools(self, body):
        tools = body.get("tools") or []
        n_orig = len(tools)
        kept = []
        for t in tools:
            name = (t.get("function") or {}).get("name") or t.get("name") or ""
            # block takes precedence: drop if matches any block pattern
            if any(p.search(name) for p in self.block):
                continue
            # if allow list non-empty, name must match at least one
            if self.allow and not any(p.search(name) for p in self.allow):
                continue
            kept.append(t)
        if kept:
            body["tools"] = kept
        else:
            body.pop("tools", None)
            body.pop("tool_choice", None)
        return body, n_orig, len(kept)

    def filter_prompt(self, body):
        if self.replace_system_prompt is not None:
            msgs = body.get("messages", [])
            for m in msgs or []:
                if m.get("role") != "system":
                    continue
                c = m.get("content")
                if isinstance(c, str):
                    before_len = len(c)
                    m["content"] = self.replace_system_prompt
                    return body, before_len, len(self.replace_system_prompt)
                if isinstance(c, list):
                    new_text = self.replace_system_prompt
                    before_len = sum(len(p.get("text") or "") for p in c if isinstance(p, dict))
                    m["content"] = [{"type": "text", "text": new_text}]
                    return body, before_len, len(new_text)
            return body, 0, 0
        if self.harness_strip:
            try:
                from alphadiana.harness.proxies.harness_strip import strip_for_harness
            except ImportError:
                # proxy may run from /tmp without package; load sibling file
                import importlib.util, os as _os
                _hs = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "harness_strip.py")
                spec = importlib.util.spec_from_file_location("harness_strip", _hs)
                mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
                strip_for_harness = mod.strip_for_harness
            msgs = body.get("messages", [])
            for m in msgs or []:
                if m.get("role") != "system":
                    continue
                c = m.get("content")
                if isinstance(c, str):
                    before = len(c)
                    m["content"] = strip_for_harness(self.harness_strip, c)
                    return body, before, len(m["content"])
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and "text" in part:
                            before = len(part["text"])
                            part["text"] = strip_for_harness(self.harness_strip, part["text"])
                            return body, before, len(part["text"])
            return body, 0, 0
        if not self.strip_tokens:
            return body, 0, 0
        msgs = body.get("messages", [])
        if not msgs:
            return body, 0, 0
        for m in msgs:
            if m.get("role") != "system":
                continue
            c = m.get("content")
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and "text" in part:
                        before = part["text"]
                        part["text"] = strip_prompt_mentions(before, self.strip_tokens)
                        return body, len(before), len(part["text"])
            elif isinstance(c, str):
                before_len = len(c)
                m["content"] = strip_prompt_mentions(c, self.strip_tokens)
                return body, before_len, len(m["content"])
            break
        return body, 0, 0

    def filter_user_intro(self, body):
        """
        Strip alphadiana-prepended block from first user message, between start of
        message and the '--- Problem ---' marker. Within that block only,
        remove sentences mentioning strip_user_intro_tokens. Problem text is
        never touched.
        """
        if not self.strip_user_intro_tokens:
            return body, 0, 0
        msgs = body.get("messages", [])
        for m in msgs:
            if m.get("role") != "user":
                continue
            c = m.get("content")
            text = c if isinstance(c, str) else (
                " ".join(p.get("text","") if isinstance(p, dict) else str(p)
                         for p in (c or []))
                if isinstance(c, list) else str(c)
            )
            if "--- Problem ---" not in text:
                # nothing to strip safely
                return body, 0, 0
            head, _, problem = text.partition("--- Problem ---")
            before_len = len(head)
            head = strip_prompt_mentions(head, self.strip_user_intro_tokens)
            new_text = (head.rstrip() + "\n\n--- Problem ---" + problem) if head.strip() \
                else ("--- Problem ---" + problem).lstrip()
            after_len = len(new_text) - len(problem) - len("--- Problem ---")
            if isinstance(c, str):
                m["content"] = new_text
            elif isinstance(c, list):
                # rewrite first text part with the full new_text, drop other parts
                m["content"] = [{"type": "text", "text": new_text}]
            return body, before_len, max(0, after_len)
        return body, 0, 0

    async def handle_chat(self, request):
        body_bytes = await request.read()
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            return web.Response(status=400, text="invalid JSON")
        # Detect if this is a follow-up turn (already has assistant tool_calls in history).
        # If so, skip --inject-final-tool-call to avoid infinite turn loop.
        already_has_tool_history = False
        for m in (body.get("messages") or []):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                already_has_tool_history = True
                break
            if m.get("role") == "tool":  # tool_result message indicates tool was called
                already_has_tool_history = True
                break
        body, n_orig, n_kept = self.filter_tools(body)
        body, p_before, p_after = self.filter_prompt(body)
        body, u_before, u_after = self.filter_user_intro(body)
        if self.model_rewrite:
            body["model"] = self.model_rewrite
        if self.ignore_providers:
            prov = body.get("provider") or {}
            ignore = list(prov.get("ignore") or [])
            for name in self.ignore_providers:
                if name not in ignore:
                    ignore.append(name)
            prov["ignore"] = ignore
            body["provider"] = prov
        if self.only_providers:
            prov = body.get("provider") or {}
            prov["only"] = list(self.only_providers)
            body["provider"] = prov
        if self.reasoning_override is not None:
            body["reasoning"] = dict(self.reasoning_override)
            # also try chat_template_kwargs.enable_thinking=false for vllm-style
            if self.reasoning_override.get("exclude"):
                eb = body.get("extra_body") or {}
                ctk = eb.get("chat_template_kwargs") or {}
                ctk["enable_thinking"] = False
                eb["chat_template_kwargs"] = ctk
                body["extra_body"] = eb
        sys.stderr.write(
            f"[filter] tools {n_orig}->{n_kept}; sys-prompt {p_before}->{p_after}; "
            f"user-intro {u_before}->{u_after}; model={body.get('model')}\n"
        )
        body_bytes = json.dumps(body).encode()
        timeout = ClientTimeout(total=3600)
        # trust_env=True lets aiohttp pick up HTTPS_PROXY / HTTP_PROXY env vars,
        # required on hosts that can only reach OpenRouter via an HTTP proxy.
        async with ClientSession(timeout=timeout, trust_env=True) as s:
            headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length", "authorization")
            }
            headers["Authorization"] = f"Bearer {self.api_key}"
            # ensure /v1 prefix on upstream URL
            base = self.upstream
            if not base.rstrip("/").endswith("/v1"):
                base = base.rstrip("/") + "/v1"
            url = base + "/chat/completions"
            async with s.post(url, data=body_bytes, headers=headers) as r:
                resp = web.StreamResponse(
                    status=r.status,
                    headers={
                        k: v for k, v in r.headers.items()
                        if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")
                    },
                )
                ctype = (r.headers.get("Content-Type") or "").lower()
                if not self.rename_reasoning:
                    # pure passthrough fast path
                    await resp.prepare(request)
                    async for chunk in r.content.iter_chunked(4096):
                        await resp.write(chunk)
                    await resp.write_eof()
                    return resp
                # rename mode = inline-think mode: convert message.reasoning into
                # <reasoning>...</reasoning> prefix inside message.content so ZC raw_response captures it
                if "text/event-stream" in ctype:
                    await resp.prepare(request)
                    # per-request state: think_state per choice index
                    # pre (no think yet) -> in (inside think) -> post (think closed)
                    think_state = {}
                    buf = b""
                    stream_aborted = False
                    try:
                        async for chunk in r.content.iter_chunked(4096):
                            buf += chunk
                            while True:
                                idx = buf.find(b"\n")
                                if idx < 0: break
                                line = buf[:idx]
                                buf = buf[idx+1:]
                                line_str = line.decode("utf-8", errors="replace")
                                if line_str.startswith("data: "):
                                    payload = line_str[6:]
                                    if payload.strip() and payload.strip() != "[DONE]":
                                        try:
                                            d = json.loads(payload)
                                            for ch in d.get("choices", []) or []:
                                                ci = ch.get("index", 0)
                                                st = think_state.get(ci, "pre")
                                                for fld in ("delta", "message"):
                                                    m = ch.get(fld)
                                                    if not isinstance(m, dict): continue
                                                    r_chunk = m.pop("reasoning", None) or m.pop("reasoning_content", None)
                                                    m.pop("reasoning_details", None)
                                                    content = m.get("content") or ""
                                                    if self.reasoning_plain:
                                                        # plain mode: no tags. Just merge reasoning into content.
                                                        if r_chunk:
                                                            m["content"] = r_chunk + (content or "")
                                                    else:
                                                        if r_chunk:
                                                            if st == "pre":
                                                                prefix = f"<{self.reasoning_tag}>" + r_chunk
                                                                st = "in"
                                                            else:
                                                                prefix = r_chunk
                                                            m["content"] = prefix + (content or "")
                                                        elif content and st == "in":
                                                            m["content"] = f"</{self.reasoning_tag}>\n" + content
                                                            st = "post"
                                                # close think tag on finish (only in tagged mode)
                                                fr = ch.get("finish_reason")
                                                if fr and st == "in" and not self.reasoning_plain:
                                                    d_target = ch.get("delta") if isinstance(ch.get("delta"), dict) else ch.get("message")
                                                    if isinstance(d_target, dict):
                                                        d_target["content"] = (d_target.get("content") or "") + f"</{self.reasoning_tag}>\n"
                                                    st = "post"
                                                think_state[ci] = st
                                                # Inject synthetic tool_call before finish_reason=stop, change finish to tool_calls.
                                                # This forces OC AI SDK to fire text-end on pure-reasoning streams that would otherwise hang.
                                                # Skip injection on follow-up turns (already saw a tool_use in this conversation).
                                                if self.inject_final_tool_call and fr == "stop" and not already_has_tool_history:
                                                    # Emit synthetic tool_call chunk BEFORE current chunk
                                                    synth_tc = {
                                                        "id": d.get("id", "chatcmpl-synth"),
                                                        "object": "chat.completion.chunk",
                                                        "created": d.get("created", int(time.time())),
                                                        "model": d.get("model", ""),
                                                        "choices": [{
                                                            "index": ci,
                                                            "delta": {
                                                                "role": "assistant",
                                                                "tool_calls": [{
                                                                    "index": 0,
                                                                    "id": "call_synth_finalize",
                                                                    "type": "function",
                                                                    "function": {"name": "_noop", "arguments": "{}"},
                                                                }],
                                                            },
                                                            "finish_reason": None,
                                                        }],
                                                    }
                                                    await resp.write(b"data: " + json.dumps(synth_tc).encode("utf-8") + b"\n\n")
                                                    # Mutate current chunk: change finish_reason to tool_calls
                                                    ch["finish_reason"] = "tool_calls"
                                            line = (b"data: " + json.dumps(d, ensure_ascii=False).encode("utf-8"))
                                        except Exception as e:
                                            pass
                                await resp.write(line + b"\n")
                    except Exception as e:
                        # OR upstream stream truncated (TransferEncodingError, ClientPayloadError, etc.)
                        # Synthesize a clean stream end so AI SDK's flush() fires and OC's run loop can terminate.
                        stream_aborted = True
                        sys.stderr.write(f"[stream-abort] {type(e).__name__}: {e}; synthesizing finish\n")
                        try:
                            # emit a synthetic finish_reason=stop chunk so AI SDK has finishReason set
                            synth = {
                                "id": "synthetic-abort",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "choices": [{"index": 0, "delta": {"content": "", "role": "assistant"}, "finish_reason": "stop"}],
                            }
                            await resp.write(b"data: " + json.dumps(synth).encode("utf-8") + b"\n\n")
                        except Exception:
                            pass
                    if buf and not stream_aborted:
                        await resp.write(buf)
                    # always emit [DONE] so SSE consumers (AI SDK) trigger flush() and finalize
                    try:
                        await resp.write(b"data: [DONE]\n\n")
                    except Exception:
                        pass
                    try:
                        await resp.write_eof()
                    except Exception:
                        pass
                    return resp
                # non-streaming JSON
                full = b""
                async for chunk in r.content.iter_chunked(4096):
                    full += chunk
                try:
                    d = json.loads(full)
                    for ch in d.get("choices", []) or []:
                        for fld in ("delta", "message"):
                            m = ch.get(fld)
                            if not isinstance(m, dict): continue
                            r_full = m.pop("reasoning", None) or m.pop("reasoning_content", None)
                            m.pop("reasoning_details", None)
                            if r_full:
                                m["content"] = f"<{self.reasoning_tag}>" + r_full + f"</{self.reasoning_tag}>\n" + (m.get("content") or "")
                    full = json.dumps(d, ensure_ascii=False).encode("utf-8")
                except Exception:
                    pass
                await resp.prepare(request)
                await resp.write(full)
                await resp.write_eof()
                return resp

    async def passthrough(self, request):
        # ensure exactly one /v1 segment between upstream base and path
        path = request.path_qs
        base = self.upstream.rstrip("/")
        if base.endswith("/v1") and path.startswith("/v1/"):
            path = path[3:]
        elif not base.endswith("/v1") and not path.startswith("/v1"):
            base = base + "/v1"
        async with ClientSession(timeout=ClientTimeout(total=60)) as s:
            url = base + path
            headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ("host", "authorization")
            }
            headers["Authorization"] = f"Bearer {self.api_key}"
            async with s.request(
                request.method, url, data=await request.read(), headers=headers
            ) as r:
                return web.Response(body=await r.read(), status=r.status)


def main(proxy_cls=None):
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--upstream", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument(
        "--allow", default="",
        help="comma-separated whitelist patterns; empty = no whitelist (use --block instead)",
    )
    p.add_argument(
        "--block", default="",
        help="comma-separated tool-name patterns to drop; takes precedence over --allow",
    )
    p.add_argument(
        "--strip-prompt-tokens", default="",
        help="comma-separated tokens to surgically remove from system prompt",
    )
    p.add_argument(
        "--strip-user-intro-tokens", default="",
        help="comma-separated tokens to remove from user-message prepended block "
             "(everything before '--- Problem ---'); problem text is never touched",
    )
    p.add_argument("--model", help="rewrite body['model'] to this if set")
    p.add_argument(
        "--harness-strip", choices=["opencode", "openclaw", "zeroclaw"], default=None,
        help="apply per-harness section-aware system-prompt strip for the no_tools cell; "
             "overrides --strip-prompt-tokens when set",
    )
    p.add_argument(
        "--ignore-providers", default="",
        help="comma-separated OpenRouter provider names to exclude (e.g. Phala for free-tier)",
    )
    p.add_argument(
        "--only-providers", default="",
        help="comma-separated OpenRouter provider names to FORCE (sets provider.only=[...])",
    )
    p.add_argument("--reasoning-exclude", action="store_true", help="set reasoning={exclude:true} (disable reasoning)")
    p.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default=None)
    p.add_argument("--reasoning-max-tokens", type=int, default=None)
    p.add_argument("--rename-reasoning", action="store_true", help="rename OR responses message.reasoning -> message.reasoning_content for ZeroClaw compatibility")
    p.add_argument("--replace-system-prompt", default=None, help="REPLACE entire system message content with this string (overrides --strip-prompt-tokens)")
    p.add_argument("--reasoning-plain", action="store_true", help="when --rename-reasoning, merge reasoning into content without <reasoning> tags (for OC AI SDK compat)")
    p.add_argument("--reasoning-tag", default="reasoning", help="tag name to wrap reasoning in delta.content; default 'reasoning' for ZC, use 'think' for OC AI SDK extractReasoningMiddleware")
    p.add_argument("--inject-final-tool-call", action="store_true", help="when stream ends with finish_reason=stop, inject synthetic tool_call chunk and change finish to tool_calls (forces OC AI SDK to fire text-end on pure-reasoning streams)")
    args = p.parse_args()
    allow = [x.strip() for x in args.allow.split(",") if x.strip()]
    block = [x.strip() for x in args.block.split(",") if x.strip()]
    strip_tokens = [x.strip() for x in args.strip_prompt_tokens.split(",") if x.strip()]
    user_intro_tokens = [x.strip() for x in args.strip_user_intro_tokens.split(",") if x.strip()]
    ignore_providers = [x.strip() for x in args.ignore_providers.split(",") if x.strip()]
    only_providers = [x.strip() for x in args.only_providers.split(",") if x.strip()]
    cls = proxy_cls or Proxy
    proxy = cls(args.upstream, args.api_key, allow, block, args.model, strip_tokens, user_intro_tokens, harness_strip=args.harness_strip, ignore_providers=ignore_providers, only_providers=only_providers)
    proxy.rename_reasoning = args.rename_reasoning
    proxy.replace_system_prompt = args.replace_system_prompt
    proxy.reasoning_plain = args.reasoning_plain
    proxy.reasoning_tag = args.reasoning_tag
    proxy.inject_final_tool_call = args.inject_final_tool_call
    if args.reasoning_exclude or args.reasoning_effort or args.reasoning_max_tokens:
        ro = {}
        if args.reasoning_exclude: ro["exclude"] = True
        if args.reasoning_effort: ro["effort"] = args.reasoning_effort
        if args.reasoning_max_tokens: ro["max_tokens"] = args.reasoning_max_tokens
        proxy.reasoning_override = ro
    app = web.Application()
    app.router.add_post("/v1/chat/completions", proxy.handle_chat)
    app.router.add_post("/chat/completions", proxy.handle_chat)
    app.router.add_route("*", "/{tail:.*}", proxy.passthrough)
    web.run_app(app, port=args.port, host="0.0.0.0")


if __name__ == "__main__":
    main()
