import sys
from pathlib import Path

# Make both the package (src/) and the repo-root webapp/ importable under pytest,
# without requiring an editable install.
ROOT = Path(__file__).parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
