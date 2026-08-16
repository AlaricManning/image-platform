import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_lambda_handler(lambda_name: str):
    """Import lambdas/<name>/handler.py under a unique module name (every
    lambda's entrypoint is handler.py, so plain imports would collide)."""
    path = REPO_ROOT / "lambdas" / lambda_name / "handler.py"
    module_name = f"{lambda_name}_handler"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
