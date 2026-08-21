from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_module_from_path(name: str, relative_path: str) -> ModuleType:
    """Load a standalone script (not a package) as a module, by its path
    relative to the project root, registering it in sys.modules under
    ``name`` so dataclasses and mock patching resolve correctly."""
    module_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
