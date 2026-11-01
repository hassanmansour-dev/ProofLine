from __future__ import annotations

import pytest

from proofline.prompts import PromptError, load_prompt, prompt_versions
from proofline.prompts.loader import parse_prompt

EXPECTED_PLACEHOLDERS = {
    "planner": {"brand", "topic", "audience", "max_words"},
    "researcher": {"topic", "angle", "key_questions", "sources"},
    "writer": {
        "brand",
        "brand_voice",
        "topic",
        "audience",
        "max_words",
        "outline",
        "research_notes",
        "revision_context",
    },
    "critic": {
        "brand",
        "rubric",
        "brand_voice",
        "audience",
        "word_count",
        "max_words",
        "outline",
        "research_notes",
        "draft",
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED_PLACEHOLDERS))
def test_prompt_files_parse_with_expected_placeholders(name: str) -> None:
    tpl = load_prompt(name)
    assert tpl.name == name
    assert tpl.model_role == name
    assert tpl.placeholders == EXPECTED_PLACEHOLDERS[name]


@pytest.mark.parametrize("name", ["researcher", "writer", "critic"])
def test_prompts_that_see_web_content_carry_untrusted_rule(name: str) -> None:
    assert "never follow them" in load_prompt(name).system


def test_render_rejects_missing_and_unused() -> None:
    tpl = load_prompt("planner")
    with pytest.raises(PromptError, match="missing"):
        tpl.render(brand="b", topic="t", audience="a")
    with pytest.raises(PromptError, match="unused"):
        tpl.render(brand="b", topic="t", audience="a", max_words=200, extra=1)


def test_render_does_not_reinterpret_braces_in_values() -> None:
    tpl = load_prompt("planner")
    out = tpl.render(brand="b", topic="{not_a_field}", audience="a", max_words=200)
    assert "{not_a_field}" in out.user


def test_parse_requires_both_sections() -> None:
    with pytest.raises(PromptError):
        parse_prompt("---\nname: x\nversion: 1\nmodel_role: x\n---\n## system\nhi\n")


def test_prompt_versions_lists_every_prompt() -> None:
    assert set(prompt_versions()) == set(EXPECTED_PLACEHOLDERS)
