"""Tests for domain/chat.py's Ollama-only tool-catalog narrowing
(_select_tools_for_provider, _last_user_content, _TOOL_CATEGORIES,
_CATEGORY_KEYWORDS) -- added after empirically confirming a real local
model (qwen2.5:3b via a real Ollama instance) got measurably less
reliable and slower as Elly's full 34-tool catalog grew, but performed
perfectly against a narrowed, category-relevant subset for the same
request. No real LLM call involved -- purely the deterministic
keyword-matching/selection logic."""

from __future__ import annotations

import pytest

from elly_server.domain.chat import (
    _ALWAYS_INCLUDE_TOOLS,
    _TOOL_CATEGORIES,
    _TOOLS,
    _last_user_content,
    _select_tools_for_provider,
)


def _names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


def test_openai_always_gets_the_full_unrestricted_catalog() -> None:
    """The whole point: OpenAI's behavior must never change, regardless
    of what the message says."""
    for content in ["create a task called X", "log that I spent $5", "hello", ""]:
        assert _select_tools_for_provider(content, "openai") == _TOOLS


def test_ollama_gets_full_catalog_when_nothing_matches() -> None:
    """An ambiguous/broad request (e.g. the chat panel's own quick-start
    prompts) must fall back to the full set -- narrowing must never make
    a genuinely cross-domain request less capable than before."""
    for content in ["Plan my day", "What should I do now?", "How am I doing?", "Brain dump"]:
        assert _select_tools_for_provider(content, "ollama") == _TOOLS


def test_ollama_narrows_to_the_task_category_for_a_clear_task_request() -> None:
    tools = _select_tools_for_provider("Create a task called Buy groceries", "ollama")
    names = _names(tools)
    assert names < _names(_TOOLS)  # a strict subset, not everything
    assert "create_task" in names
    assert _TOOL_CATEGORIES["tasks"] <= names
    # a category that clearly isn't relevant should be excluded
    assert "log_expense" not in names
    assert "create_habit" not in names


def test_ollama_narrows_to_budget_category_for_an_expense() -> None:
    tools = _select_tools_for_provider("I spent $12 on lunch today", "ollama")
    names = _names(tools)
    assert _TOOL_CATEGORIES["budget"] <= names
    assert "create_task" not in names


def test_ollama_combines_categories_when_multiple_match() -> None:
    tools = _select_tools_for_provider("create a task and log my water habit", "ollama")
    names = _names(tools)
    assert _TOOL_CATEGORIES["tasks"] <= names
    assert _TOOL_CATEGORIES["habits"] <= names


def test_always_include_tools_present_whenever_narrowed() -> None:
    tools = _select_tools_for_provider("create a task called X", "ollama")
    names = _names(tools)
    assert _ALWAYS_INCLUDE_TOOLS <= names


def test_every_tool_category_actually_matches_real_tool_names() -> None:
    """Guards against the category map drifting out of sync with the
    real _TOOLS list (e.g. a tool renamed in _build_tools() but not
    here) -- every name in every category must be a real, defined tool."""
    all_tool_names = {t["function"]["name"] for t in _TOOLS}
    for category, names in _TOOL_CATEGORIES.items():
        missing = names - all_tool_names
        assert not missing, f"category {category!r} references undefined tools: {missing}"


def test_last_user_content_finds_the_most_recent_user_turn() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply", "tool_calls": []},
        {"role": "tool", "content": "result"},
        {"role": "user", "content": "second, the real one"},
    ]
    assert _last_user_content(messages) == "second, the real one"


def test_last_user_content_empty_when_no_user_message() -> None:
    assert _last_user_content([{"role": "system", "content": "sys"}]) == ""


@pytest.mark.parametrize("content,expected_category", [
    ("add a diary entry about today", "notes"),
    ("schedule a meeting for tomorrow", "calendar"),
    ("break down this task for me", "tasks"),
    ("I kept my streak going today", "habits"),
    ("what's my mood trend been like", "insights"),
    ("remember that I prefer mornings", "memory"),
])
def test_representative_phrasings_match_the_expected_category(content: str, expected_category: str) -> None:
    tools = _select_tools_for_provider(content, "ollama")
    names = _names(tools)
    assert _TOOL_CATEGORIES[expected_category] <= names
