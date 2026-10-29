"""Put the skill's ``scripts/`` dir on sys.path so tests import model/classify/etc. directly.

These tests are stdlib-only (no Spark, no datalab framework) and exercise the Alteryx→YAML
generator in isolation, mirroring the repo's ``scripts/tests`` integrity tests.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Absolute path to the bundled worked example, used across tests.
REPO_ROOT = SCRIPTS.parents[3]  # .agents/skills/<name>/scripts -> repo root
EXAMPLE_CONTEXT = (
    REPO_ROOT / "migrations" / "alteryx" / "out" / "Master_ONB_BA (1)" / "conversion_context.json"
)
