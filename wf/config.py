"""`.workflow/project.toml`: what the project runs (install, suite) and how wf names things.

Written by `wf init`, edited by a person. Unknown keys are ignored so an older kit still reads a newer file.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import Refused, WfError

ROLES = ("manager", "specifier", "planner", "builder", "reviewer", "tester", "shipper")
STAGE_OWNER = {
    "spec": "specifier",
    "plan": "planner",
    "build": "builder",
    "review": "reviewer",
    "test": "tester",
    "ship": "shipper",
    "done": "manager",
    "cancelled": "manager",
}
_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Config:
    project: Path
    kit: Path | None = None
    main: str = "main"
    install: str = ""
    suite: tuple[str, ...] = ()
    e2e: str = ""
    start: str = ""
    max_loops: int = 3
    run_timeout: float = 600.0
    ports: tuple[int, ...] = ()
    rules: str = ""
    aliases: dict[str, str] = field(default_factory=dict)

    def alias(self, role: str) -> str:
        return self.aliases.get(role, role)

    def role_of(self, name: str) -> str | None:
        """The role for NAME, given as a role or as its alias (an @ in front is ignored)."""
        name = name.strip().lstrip("@")
        if name in ROLES:
            return name
        for role in ROLES:
            if self.alias(role) == name:
                return role
        return None

    def rules_path(self) -> Path | None:
        if not self.rules:
            return None
        path = Path(self.rules).expanduser()
        if not path.is_absolute() and self.kit is not None:
            path = self.kit / path
        return path

    def unknown_alias(self, name: str) -> Refused:
        names = ", ".join(self.alias(role) for role in ROLES)
        return Refused(f"Unknown agent {name.strip() or '(empty)'}: use one of {names}.")


def config_path(project: Path) -> Path:
    return project / ".workflow" / "project.toml"


def load_config(project: Path) -> Config:
    path = config_path(project)
    if not path.is_file():
        raise WfError(
            "This folder is not set up for wf (no .workflow/project.toml). "
            "From the kit run: python3 -m wf init <this folder>"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise WfError(f".workflow/project.toml cannot be read: {exc}") from None
    return parse_config(project, data)


def parse_config(project: Path, data: dict) -> Config:
    problems: list[str] = []

    def text(key: str, default: str = "") -> str:
        value = data.get(key, default)
        if not isinstance(value, str):
            problems.append(f"{key} must be text")
            return default
        return value.strip()

    suite_value = data.get("suite", [])
    if isinstance(suite_value, str):
        suite_value = [suite_value]
    if not isinstance(suite_value, list) or not all(isinstance(s, str) for s in suite_value):
        problems.append("suite must be a list of commands")
        suite_value = []
    max_loops = data.get("max_loops", 3)
    if not isinstance(max_loops, int) or isinstance(max_loops, bool) or max_loops < 1:
        problems.append("max_loops must be a whole number, 1 or more")
        max_loops = 3
    run_timeout = data.get("run_timeout", 600)
    if not isinstance(run_timeout, (int, float)) or isinstance(run_timeout, bool) or run_timeout <= 0:
        problems.append("run_timeout must be a number of seconds")
        run_timeout = 600
    ports = data.get("ports", [])
    if not isinstance(ports, list) or not all(isinstance(p, int) and not isinstance(p, bool) and 0 < p < 65536 for p in ports):
        problems.append("ports must be a list of port numbers")
        ports = []
    aliases: dict[str, str] = {}
    raw_aliases = data.get("aliases", {})
    if not isinstance(raw_aliases, dict):
        problems.append("[aliases] must be a table")
        raw_aliases = {}
    for role, alias in raw_aliases.items():
        if role not in ROLES:
            problems.append(f"[aliases] {role} is not one of {', '.join(ROLES)}")
        elif not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            problems.append(f"[aliases] {role} must be letters, digits, dot, dash or underscore")
        else:
            aliases[role] = alias
    if len(set(aliases.get(r, r) for r in ROLES)) != len(ROLES):
        problems.append("[aliases] two agents have the same alias")
    main = text("main", "main") or "main"
    kit_text = text("kit")
    if problems:
        raise WfError(".workflow/project.toml: " + "; ".join(problems))
    return Config(
        project=project,
        kit=Path(kit_text).expanduser() if kit_text else None,
        main=main,
        install=text("install"),
        suite=tuple(s.strip() for s in suite_value if s.strip()),
        e2e=text("e2e"),
        start=text("start"),
        max_loops=max_loops,
        run_timeout=float(run_timeout),
        ports=tuple(ports),
        rules=text("rules"),
        aliases=aliases,
    )
