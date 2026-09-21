"""Parser, lint and rendering for questions (.md files with YAML front-matter).

The format is documented in README.md. Depends only on PyYAML.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REQUIRED_META = ("id", "domain", "topic", "difficulty", "points", "namespaces")
DOMAINS = {
    "application-design-build",
    "application-deployment",
    "application-observability-maintenance",
    "application-environment-config-security",
    "services-networking",
}
VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
# kubectl go-template syntax that matches VAR_RE but is not one of our variables.
GO_TEMPLATE_WORDS = {"end", "else"}


class QuestionError(ValueError):
    pass


@dataclass
class Check:
    points: int
    description: str
    command: str


@dataclass
class Question:
    path: Path
    meta: dict
    statement: str
    setup: str
    checks: list[Check]
    solutions: dict[str, str]
    values: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return str(self.meta.get("id", self.path.stem))

    @property
    def total_points(self) -> int:
        return sum(c.points for c in self.checks)

    def render(self, seed: int) -> "Question":
        """Draw the `vars` (deterministic per seed+id) and substitute {{var}}."""
        values = pick_vars(self.meta, seed)

        def sub(text: str) -> str:
            return VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)

        meta = dict(self.meta)
        meta["namespaces"] = [sub(str(n)) for n in self.meta.get("namespaces") or []]
        return Question(
            path=self.path,
            meta=meta,
            statement=sub(self.statement),
            setup=sub(self.setup),
            checks=[Check(c.points, sub(c.description), sub(c.command)) for c in self.checks],
            solutions={name: sub(script) for name, script in self.solutions.items()},
            values=values,
        )


def pick_vars(meta: dict, seed: int) -> dict[str, str]:
    rng = random.Random(f"{seed}:{meta.get('id')}")
    values: dict[str, str] = {}
    for name, options in (meta.get("vars") or {}).items():
        if not isinstance(options, list) or not options:
            raise QuestionError(f"vars.{name} must be a non-empty list")
        values[name] = str(rng.choice(options))
    return values


def _split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise QuestionError("missing YAML front-matter at the start of the file (---)")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise QuestionError("YAML front-matter was not closed (---)")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        raise QuestionError(f"invalid front-matter: {e}") from e
    if not isinstance(meta, dict):
        raise QuestionError("front-matter must be a YAML mapping")
    return meta, text[end + 5:]


def _split_sections(body: str) -> dict[str, str]:
    """Split into '## Title' sections, ignoring '## ' inside code blocks."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            current = line[3:].strip()
            if current in sections:
                raise QuestionError(f"duplicate section: '{current}'")
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {title: "\n".join(lines).strip("\n") for title, lines in sections.items()}


def _code(section: str) -> str:
    """Concatenate the contents of the ``` blocks of a section."""
    blocks: list[str] = []
    buf: list[str] = []
    in_fence = False
    for line in section.splitlines():
        if line.startswith("```"):
            if in_fence:
                blocks.append("\n".join(buf))
                buf = []
            in_fence = not in_fence
        elif in_fence:
            buf.append(line)
    if in_fence:
        raise QuestionError("code block not closed (```)")
    return "\n".join(blocks)


def _parse_checks(code: str) -> list[Check]:
    checks = []
    for n, raw in enumerate(code.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 2)  # the command may contain '|'
        if len(parts) != 3 or not parts[0].strip().isdigit():
            raise QuestionError(f"Check, line {n}: expected 'points|description|command'")
        checks.append(Check(int(parts[0]), parts[1].strip(), parts[2].strip()))
    return checks


def load(path: Path) -> Question:
    try:
        meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
        sections = _split_sections(body)
        solutions = {}
        for title, content in sections.items():
            if title.startswith("Solution"):
                solutions[title.partition(":")[2].strip() or "default"] = _code(content)
        return Question(
            path=path,
            meta=meta,
            statement=sections.get("Statement", "").strip(),
            setup=_code(sections.get("Setup", "")),
            checks=_parse_checks(_code(sections.get("Check", ""))),
            solutions=solutions,
        )
    except QuestionError as e:
        raise QuestionError(f"{path}: {e}") from e


def discover(paths: list[Path]) -> list[Path]:
    """The .md question files; skips README and files starting with '_'."""
    found: list[Path] = []
    for p in paths:
        files = sorted(p.rglob("*.md")) if p.is_dir() else [p]
        found += [f for f in files if not f.name.startswith("_") and f.name != "README.md"]
    return found


def is_protected_namespace(ns: str) -> bool:
    return ns == "default" or ns.startswith("kube-")


def lint(q: Question) -> tuple[list[str], list[str]]:
    """Return (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_META:
        if key not in q.meta:
            errors.append(f"front-matter: missing '{key}'")
    if errors:
        return errors, warnings

    if q.meta["id"] != q.path.stem:
        errors.append(f"id '{q.meta['id']}' must equal the file name '{q.path.stem}'")
    if q.meta["domain"] not in DOMAINS:
        errors.append(f"unknown domain '{q.meta['domain']}' (valid: {', '.join(sorted(DOMAINS))})")
    if q.meta["difficulty"] not in (1, 2, 3):
        errors.append("difficulty must be 1, 2 or 3")
    if not isinstance(q.meta["namespaces"], list) or not q.meta["namespaces"]:
        errors.append("namespaces must be a non-empty list (the validator resets these namespaces)")
    if not q.statement:
        errors.append("'Statement' section is empty or missing")
    if not q.checks:
        errors.append("'Check' section is empty or missing")
    elif q.total_points != q.meta["points"]:
        errors.append(f"points={q.meta['points']} in the front-matter, but the checks add up to {q.total_points}")
    if not q.solutions:
        errors.append("no 'Solution' section (use '## Solution: name')")
    elif len(q.solutions) == 1:
        warnings.append("only one solution: the checker was not tested against an alternative way of solving it")
    if errors:
        return errors, warnings

    try:
        r = q.render(0)
    except QuestionError as e:
        return [str(e)], warnings
    for ns in r.meta["namespaces"]:
        if is_protected_namespace(ns):
            errors.append(f"protected namespace in 'namespaces': {ns}")
    texts = [r.statement, r.setup, *(c.command for c in r.checks), *r.solutions.values()]
    left = {m for t in texts for m in VAR_RE.findall(t)} - GO_TEMPLATE_WORDS
    if left:
        warnings.append(f"possibly undefined variable in vars: {', '.join(sorted(left))}")
    return errors, warnings
