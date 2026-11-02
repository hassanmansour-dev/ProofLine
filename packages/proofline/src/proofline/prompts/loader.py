"""Load versioned prompt files and render them strictly (ADR-0010).

A prompt file is YAML frontmatter + `## system` + `## user`. Placeholders are
`{name}`; literal braces must be doubled. Rendering fails on a missing OR an
unused variable, so a renamed placeholder can't silently drop data.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from proofline.llm import RenderedPrompt

PROMPTS_DIR = Path(__file__).parent
_SECTION_RE = re.compile(r"^## (system|user)\s*$", re.MULTILINE)


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    model_role: str
    system: str
    user: str

    @property
    def placeholders(self) -> frozenset[str]:
        return _fields(self.system) | _fields(self.user)

    def render(self, **variables: Any) -> RenderedPrompt:
        provided = set(variables)
        missing = self.placeholders - provided
        unused = provided - self.placeholders
        if missing or unused:
            raise PromptError(
                f"{self.name}@{self.version}: missing={sorted(missing)} unused={sorted(unused)}"
            )
        values = {k: str(v) for k, v in variables.items()}
        return RenderedPrompt(
            name=self.name,
            version=self.version,
            system=self.system.format_map(values),
            user=self.user.format_map(values),
        )


def _fields(text: str) -> frozenset[str]:
    return frozenset(f for _, f, _, _ in string.Formatter().parse(text) if f)


def parse_prompt(text: str, *, source: str = "<string>") -> PromptTemplate:
    if not text.startswith("---"):
        raise PromptError(f"{source}: missing YAML frontmatter")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front) or {}
    for key in ("name", "version", "model_role"):
        if key not in meta:
            raise PromptError(f"{source}: frontmatter needs '{key}'")

    parts = _SECTION_RE.split(body)
    # ['', 'system', '<text>', 'user', '<text>']
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise PromptError(f"{source}: needs exactly '## system' and '## user' sections")
    return PromptTemplate(
        name=str(meta["name"]),
        version=str(meta["version"]),
        model_role=str(meta["model_role"]),
        system=sections["system"],
        user=sections["user"],
    )


@lru_cache
def load_prompt(name: str) -> PromptTemplate:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptError(f"no prompt file {path}")
    return parse_prompt(path.read_text(encoding="utf-8"), source=str(path))


def prompt_versions() -> dict[str, str]:
    """Versions of every prompt file — recorded in run metadata and FinalScript."""
    out: dict[str, str] = {}
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        if path.name == "CHANGELOG.md":
            continue
        tpl = load_prompt(path.stem)
        out[tpl.name] = tpl.version
    return out
