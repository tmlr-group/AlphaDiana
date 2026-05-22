"""Per-harness section-aware system-prompt strip for the no_tools micro cell.

Each harness exposes a different system prompt structure. Token-based regex
strips either over-delete or under-delete. This module surgically removes:
- whole sections that are 100% tool documentation, by matching their literal headers
- specific sentences/paragraphs/example blocks that mix tool affordances into otherwise
  KEEP-worthy sections, by literal string replace

Verbatim deletion strings come from sections reviewed manually against captured
system-prompt dumps (one per harness) on 2026-05-03.
"""
from __future__ import annotations
import re

_HEADER_RE = re.compile(
    r'^(#+\s+|=+\s|---+|\*\*[^*]+\*\*\s*$|<[a-zA-Z_]+>\s*$|[A-Z][A-Z _]+:\s*$)'
)


def _split_sections(text: str):
    sections = []
    cur_h, cur_body = '(prelude)', []
    for ln in text.split('\n'):
        s = ln.strip()
        if _HEADER_RE.match(s) and len(s) < 100:
            sections.append((cur_h, cur_body))
            cur_h, cur_body = s, [ln]
        else:
            cur_body.append(ln)
    sections.append((cur_h, cur_body))
    return sections


def _drop_sections(text: str, headers_to_drop):
    drop = set(headers_to_drop)
    out = []
    for h, body in _split_sections(text):
        if h in drop:
            continue
        out.extend(body)
    return '\n'.join(out)


def _drop_literals(text: str, literals):
    for s in literals:
        text = text.replace(s, '')
    return re.sub(r'\n{3,}', '\n\n', text)


# ============================================================
# ZeroClaw
# ============================================================

_ZC_DROP_SECTIONS = [
    '## CRITICAL: No Tool Narration',
    '## CRITICAL: Tool Honesty',
    '## Tools',
]

_ZC_DROP_LITERALS = [
    'Use tools when the request requires action (running commands, reading files, etc.).',
    '- If the runtime policy already allows a tool, use it directly; do not ask the user for extra approval.',
    '- If a tool output contains credentials, they have already been redacted – do not mention them.',
    "- NEVER narrate or describe your tool usage. Do NOT say 'Let me fetch...', 'I will use...', 'Searching...', or similar. Give the FINAL ANSWER only — no intermediate steps, no tool mentions, no progress updates.",
]


def strip_zeroclaw(sys_text: str) -> str:
    out = _drop_sections(sys_text, _ZC_DROP_SECTIONS)
    out = _drop_literals(out, _ZC_DROP_LITERALS)
    return out.strip() + '\n'


# ============================================================
# OpenClaw
# ============================================================

_OW_DROP_SECTIONS = [
    '## Tooling',
    '## Tool Call Style',
    '## OpenClaw CLI Quick Reference',
    '## Skills (mandatory)',
    '<available_skills>',
    '<skill>',
    '## Tools',
]


def strip_openclaw(sys_text: str) -> str:
    out = _drop_sections(sys_text, _OW_DROP_SECTIONS)
    out = out.replace('</available_skills>', '')
    out = re.sub(r'\n{3,}', '\n\n', out)
    return out.strip() + '\n'


# ============================================================
# OpenCode
# ============================================================

_OC_DROP_SECTIONS = ['# Tool usage policy']

_OC_DROP_EXAMPLES = [
    '<example>\nuser: what command should I run to list files in the current directory?\nassistant: ls\n</example>',
    '<example>\nuser: what command should I run to watch files in the current directory?\nassistant: [use the ls tool to list the files in the current directory, then read docs/commands in the relevant file to find out how to watch files]\nnpm run dev\n</example>',
    '<example>\nuser: what files are in the directory src/?\nassistant: [runs ls and sees foo.c, bar.c, baz.c]\nuser: which file contains the implementation of foo?\nassistant: src/foo.c\n</example>',
    '<example>\nuser: write tests for new feature\nassistant: [uses grep and glob search tools to find where similar tests are defined, uses concurrent read file tool use blocks in one tool call to read relevant files at the same time, uses edit file tool to write new tests]\n</example>',
]

_OC_DROP_LITERALS = [
    "When you run a non-trivial bash command, you should explain what the command does and why you are running it, to make sure the user understands what you are doing (this is especially important when you are running a command that will make changes to the user's system).",
    'Output text to communicate with the user; all text you output outside of tool use is displayed to the user. Only use tools to complete tasks. Never use tools like Bash or code comments as means to communicate with the user during the session.',
    "- Use the available search tools to understand the codebase and the user's query. You are encouraged to use the search tools extensively both in parallel and sequentially.",
    '- Implement the solution using all tools available to you',
    '- VERY IMPORTANT: When you have completed a task, you MUST run the lint and typecheck commands (e.g. npm run lint, npm run typecheck, ruff, etc.) with Bash if they were provided to you to ensure your code is correct. If you are unable to find the correct command, ask the user for the command to run and if they supply it, proactively suggest writing it to AGENTS.md so that you will know to run it next time.',
    "- Tool results and user messages may include <system-reminder> tags. <system-reminder> tags contain useful information and reminders. They are NOT part of the user's provided input or the tool result.",
    'Skills provide specialized instructions and workflows for specific tasks.',
    'Use the skill tool to load a skill when a task matches its description.',
    'No skills are currently available.',
]


def strip_opencode(sys_text: str) -> str:
    out = _drop_sections(sys_text, _OC_DROP_SECTIONS)
    out = _drop_literals(out, _OC_DROP_EXAMPLES + _OC_DROP_LITERALS)
    return out.strip() + '\n'


_DISPATCH = {
    'zeroclaw': strip_zeroclaw,
    'openclaw': strip_openclaw,
    'opencode': strip_opencode,
}


def strip_for_harness(harness: str, sys_text: str) -> str:
    fn = _DISPATCH.get(harness)
    return fn(sys_text) if fn else sys_text
