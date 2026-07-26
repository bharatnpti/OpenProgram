from __future__ import annotations

from core.application.json_parsing import extract_json_object


def test_extract_plain_object() -> None:
    assert extract_json_object('{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}


def test_extract_json_fenced_object() -> None:
    text = '```json\n{"sufficient": true, "question": null}\n```'
    assert extract_json_object(text) == {"sufficient": True, "question": None}


def test_extract_bare_fenced_object() -> None:
    text = '```\n{"progress_note": "done"}\n```'
    assert extract_json_object(text) == {"progress_note": "done"}


def test_extract_object_from_surrounding_prose() -> None:
    text = 'Sure, here is the result: {"sufficient": false} — let me know if that helps.'
    assert extract_json_object(text) == {"sufficient": False}


def test_extract_first_balanced_object_with_nested_braces_and_strings() -> None:
    text = (
        'noise {"outer": {"inner": 1}, "note": "has } brace and \\" quote"} trailing {"second": 2}'
    )
    assert extract_json_object(text) == {
        "outer": {"inner": 1},
        "note": 'has } brace and " quote',
    }


def test_extract_returns_none_for_non_object_json() -> None:
    assert extract_json_object('["not", "an", "object"]') is None


def test_extract_returns_none_for_unparseable_text() -> None:
    assert extract_json_object("totally not json at all") is None


def test_extract_returns_none_for_unbalanced_object() -> None:
    assert extract_json_object('{"a": 1') is None


def test_extract_returns_none_for_empty_text() -> None:
    assert extract_json_object("") is None
