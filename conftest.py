"""Root conftest - ensures the custom_components directory is importable."""

import sys
from pathlib import Path

# Add the project root to sys.path so custom_components can be found
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
