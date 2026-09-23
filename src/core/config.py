#!/usr/bin/env python3
"""
config.py — read jobs/config.yaml with safe fallbacks.

Every operational number in the pipeline comes through here, so behaviour is
changed by editing config.yaml rather than by finding it in a script. A missing
key falls back to the default passed at the call site, so deleting a line from
the YAML degrades gracefully instead of raising.

    from jobpilot.core.config import cfg
    limit = cfg("pipeline.tailor_limit", 6)
"""
import os
import functools

import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
PATH = os.path.join(CONFIG, "config.yaml")


@functools.lru_cache(maxsize=1)
def _load():
    try:
        with open(PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def cfg(dotted, default=None):
    """cfg("pipeline.tailor_limit", 6) -> value or default."""
    node = _load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default


def reload():
    _load.cache_clear()
    return _load()


if __name__ == "__main__":
    import json
    print(json.dumps(_load(), indent=2, default=str))
