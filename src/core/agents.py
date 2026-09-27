#!/usr/bin/env python3
"""
agents.py — every Claude session the pipeline starts is an AGENT, and each one
lives in its own folder, src/agents/<name>/, not in the code:

    agents/<name>/prompt.md     the prompt, with {placeholders} the code fills in
    agents/<name>/tools.yaml    what the session may use and how it is run:
        llm: explore            config.yaml llm.<key> -> --model / --effort
        max_turns: true         true -> llm.max_turns; a number; false -> no cap
        timeout: fill.explore_timeout_s   a config key, or seconds
        add_dirs: ["{tool}"]
        allowed_tools: [Read, Glob, "Edit(/{appdir}/**)", ...]
        params: [slug, url, ...]          what the prompt needs; render() refuses to run without them
    agents/<name>/*.md          further text the code picks from (a toolkit per platform,
                                a situation per mode) — read with part(name)

Rendering replaces only {name} for the params given, so a prompt can show JSON
or LaTeX braces as they are. A param the prompt declares but the caller did not
pass is an error, not a silently empty hole in the prompt.
"""
import os
import re

import yaml

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")   # src/agents
_PARAM = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class Agent:
    def __init__(self, name):
        self.name = name
        self.dir = os.path.join(ROOT, name)
        with open(os.path.join(self.dir, "prompt.md"), encoding="utf-8") as f:
            self.template = f.read()
        try:
            with open(os.path.join(self.dir, "tools.yaml"), encoding="utf-8") as f:
                self.spec = yaml.safe_load(f) or {}
        except FileNotFoundError:
            self.spec = {}

    def part(self, name, default=""):
        """Another text file in this agent's folder (a toolkit, a situation), or default."""
        p = os.path.join(self.dir, name if name.endswith(".md") else name + ".md")
        try:
            with open(p, encoding="utf-8") as f:
                return f.read().rstrip()
        except FileNotFoundError:
            return default

    def render(self, **kw):
        """The prompt with its params filled in."""
        return fill(self.template, kw, self.spec.get("params"), where=f"src/agents/{self.name}/prompt.md")

    def timeout(self, default=900):
        t = self.spec.get("timeout", default)
        return int(cfg(t, default)) if isinstance(t, str) else int(t)

    def argv(self, cli, prompt, **kw):
        """The full `claude -p` command line for this agent."""
        from jobpilot.tailor.autotailor import llm_flags
        out = [cli, "-p", prompt]
        if self.spec.get("llm"):
            out += llm_flags(self.spec["llm"])
        mt = self.spec.get("max_turns", False)
        if mt is True:
            mt = int(cfg("llm.max_turns", 20))
        if mt:
            out += ["--max-turns", str(int(mt))]
        for d in self.spec.get("add_dirs") or []:
            out += ["--add-dir", fill(d, kw)]
        tools = [fill(t, kw) for t in self.spec.get("allowed_tools") or []]
        if tools:
            out += ["--allowedTools", ",".join(tools)]
        return out + ["--output-format", "text"]


def fill(text, kw, required=None, where="agent text"):
    kw = {"tool": TOOL, **kw}
    missing = [p for p in (required or []) if p not in kw]
    if missing:
        raise KeyError(f"{where} needs {missing}")
    return _PARAM.sub(lambda m: str(kw[m.group(1)]) if m.group(1) in kw else m.group(0), text)


_CACHE = {}


def get(name):
    """The agent named `name` (read from disk once per process)."""
    if name not in _CACHE:
        _CACHE[name] = Agent(name)
    return _CACHE[name]


def reload():
    _CACHE.clear()
