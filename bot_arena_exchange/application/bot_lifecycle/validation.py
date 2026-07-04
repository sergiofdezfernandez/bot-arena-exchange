import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional


ALLOWED_IMPORT_ROOTS = {
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "fractions",
    "math",
    "random",
    "statistics",
    "typing",
}


@dataclass(frozen=True)
class BotFile:
    path: str
    content: str


@dataclass(frozen=True)
class BotValidationResult:
    passed: bool
    errors: List[str] = field(default_factory=list)


class BotValidator:
    def __init__(self, allowed_import_roots: Optional[set] = None):
        self.allowed_import_roots = allowed_import_roots or ALLOWED_IMPORT_ROOTS

    def validate(self, files: Dict[str, str]) -> BotValidationResult:
        errors = []
        if not files:
            return BotValidationResult(False, ["submission must include at least one Python file"])

        python_files = {path: content for path, content in files.items() if path.endswith(".py")}
        if not python_files:
            return BotValidationResult(False, ["submission must include at least one Python file"])

        main_content = python_files.get("bot.py") or next(iter(python_files.values()))
        try:
            tree = ast.parse(main_content)
        except SyntaxError as exc:
            return BotValidationResult(False, [f"syntax error: {exc.msg}"])

        entry_points = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_bot"]
        if not entry_points:
            errors.append("bot must define create_bot()")
        elif entry_points[0].args.args or entry_points[0].args.vararg or entry_points[0].args.kwarg:
            errors.append("create_bot() must not require arguments")

        for path, content in python_files.items():
            try:
                parsed = ast.parse(content)
            except SyntaxError as exc:
                errors.append(f"{path}: syntax error: {exc.msg}")
                continue
            errors.extend(self._validate_imports(path, parsed))

        return BotValidationResult(not errors, errors)

    def _validate_imports(self, path: str, tree) -> List[str]:
        errors = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in self.allowed_import_roots:
                        errors.append(f"{path}: unsupported dependency '{root}'")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root and root not in self.allowed_import_roots:
                    errors.append(f"{path}: unsupported dependency '{root}'")
        return errors
