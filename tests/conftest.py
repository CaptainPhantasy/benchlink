import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/smoke"))
import benchlink_testkit  # noqa: E402,F401  (sets app import paths)
