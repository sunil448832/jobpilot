"""
paths.py — the one place that knows where things live.

jobpilot is two trees, deliberately:

  TOOL      this checkout: code, config, data, and — since 2026-09-22 — the
            tool's own outputs: applications/<slug>/ and tracking/*.xlsx.
  TRACKING  Sunil's resume repo: resume/, project-memory-backup/,
            target-companies/. READ-ONLY for the tool: the base resume and the
            code-grounded notes are the only things the tool takes from it.

Every module used to derive the tracking repo as dirname(dirname(__file__)) —
true only while the code lived INSIDE that repo. Moving the tool out silently
pointed every path at /home/sunil/work/projects. So: nothing derives a path
from its own location any more except this file, and the tracking repo is
configured, not inferred.

Resolution order for TRACKING:
  1. $JOBPILOT_TRACKING
  2. config/config.yaml  ->  paths.tracking_repo
  3. ~/work/docs/sunil_resume_v2
"""
import os

# This file is src/core/paths.py; src/ is the package root, the tool is above it.
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.dirname(SRC)
CONFIG = os.path.join(TOOL, "config")
DATA = os.path.join(TOOL, "data")
SCRIPTS = os.path.join(TOOL, "scripts")
POLICY = os.path.join(CONFIG, "POLICY.md")     # hand-edited rules live with the other hand-edited files

_DEFAULT_TRACKING = os.path.expanduser("~/work/docs/sunil_resume_v2")


def _tracking():
    env = os.environ.get("JOBPILOT_TRACKING")
    if env:
        return os.path.expanduser(env)
    try:
        import yaml
        with open(os.path.join(CONFIG, "config.yaml")) as f:
            v = ((yaml.safe_load(f) or {}).get("paths") or {}).get("tracking_repo")
        if v:
            return os.path.expanduser(v)
    except Exception:
        pass
    return _DEFAULT_TRACKING


TRACKING = _tracking()

# What the tool READS from the resume repo ...
RESUME = os.path.join(TRACKING, "resume")
MEMORY = os.path.join(TRACKING, "project-memory-backup")
# ... and what it WRITES, which lives with the tool.
APPLICATIONS = os.path.join(TOOL, "applications")
TRACKERS = os.path.join(TOOL, "tracking")

os.makedirs(DATA, exist_ok=True)


def check():
    """Loud, early failure beats a NameError three modules deep."""
    missing = [p for p in (TRACKING, RESUME, MEMORY) if not os.path.isdir(p)]
    if missing:
        raise SystemExit("jobpilot: tracking repo not found:\n  "
                         + "\n  ".join(missing)
                         + "\nset paths.tracking_repo in config/config.yaml "
                           "or export JOBPILOT_TRACKING")


if __name__ == "__main__":
    for k in ("TOOL", "SRC", "CONFIG", "DATA", "TRACKING", "RESUME", "APPLICATIONS",
              "TRACKERS", "MEMORY", "POLICY"):
        v = globals()[k]
        print(f"  {k:<13} {v}   {'ok' if os.path.exists(v) else 'MISSING'}")
