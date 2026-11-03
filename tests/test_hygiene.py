"""Guards on the repo itself: no secret-shaped text anywhere, and the docs list every check type."""

from __future__ import annotations

import re
import unittest

from helpers import ROOT
from preflight.checks import CHECK_TYPES
from preflight.checks.secrets import find_secrets

SKIP_DIRS = {".git", "__pycache__", ".preflight", ".venv", ".idea", ".vscode"}


def repo_files() -> list:
    return [p for p in sorted(ROOT.rglob("*")) if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts) and p.suffix != ".pyc"]


class HygieneTests(unittest.TestCase):
    def test_no_file_in_the_repo_contains_a_secret_shaped_string(self) -> None:
        found = []
        for path in repo_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                found += [f"{path.relative_to(ROOT)}:{number} {hit.pattern}" for hit in find_secrets(line)]
        self.assertEqual(found, [])

    def test_the_readme_documents_every_check_type_and_the_exit_codes(self) -> None:
        readme = (ROOT / "preflight" / "README.md").read_text(encoding="utf-8")
        for name in CHECK_TYPES:
            self.assertIn(f"`{name}`", readme, f"{name} is not documented in preflight/README.md")
        for word in ("verdict.json", "GATE OK", "GATE CLOSED", "--offline", "--only", "--no-write", ".preflight-allow", "extends"):
            self.assertIn(word, readme)

    def test_every_module_has_a_docstring_and_type_hints_on_public_functions(self) -> None:
        import ast

        for path in sorted((ROOT / "preflight").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(ast.get_docstring(tree), f"{path.name} needs a module docstring")
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    self.assertIsNotNone(node.returns, f"{path.name}:{node.name} needs a return annotation")


if __name__ == "__main__":
    unittest.main()
