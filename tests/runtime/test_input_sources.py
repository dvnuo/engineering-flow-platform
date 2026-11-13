"""The shared checks for `source` and `params`, as the runtime sees them.

The card loader and the question tool test the rules through their own inputs;
what is pinned here is the part neither can show: the helpers judge a value by
what it is (a mapping, a list-like), not by the class one particular YAML or
JSON loader happens to return.
"""

from __future__ import annotations

from collections import UserList
from types import MappingProxyType

import pytest

from efp_runtime.input_sources import (
    MAX_PARAMS,
    MAX_SOURCE_CHARS,
    clean_params,
    clean_source,
    is_name,
    is_sequence,
)


@pytest.mark.parametrize("value", [[], ["a"], ("a", "b"), UserList(["a"])])
def test_list_likes_are_sequences(value):
    assert is_sequence(value) is True


@pytest.mark.parametrize("value", ["abc", b"abc", bytearray(b"abc"), {"a": 1}, {"a"}, 5, None])
def test_text_bytes_and_mappings_are_not_sequences(value):
    assert is_sequence(value) is False


def test_source_is_matched_as_written():
    assert clean_source("jira.issues") == "jira.issues"
    assert clean_source("j" * MAX_SOURCE_CHARS) == "j" * MAX_SOURCE_CHARS
    for bad in ["j" * (MAX_SOURCE_CHARS + 1), "jira.issues ", "jira.issues\n", "Jira.issues", "", None, 5, ["jira"]]:
        assert clean_source(bad) == ""


def test_names_are_lower_case_identifiers_of_at_most_32_characters():
    assert all(is_name(value) for value in ["a", "a1", "a_b", "n" * 32])
    assert not any(is_name(value) for value in ["", "1a", "_a", "A", "a-b", "a.b", "n" * 33, "a\n", None, 5])


def test_params_accept_any_mapping_and_any_list_like():
    params = MappingProxyType({"types": ("Epic", " Story "), "labels": UserList(["a", ""]), "open": False, "limit": 0})

    assert clean_params(params) == {
        "types": ["Epic", "Story"],
        "labels": ["a"],
        "open": False,
        "limit": 0,
    }


def test_params_keep_the_first_usable_entries_up_to_the_cap():
    params = {"Bad": "skipped", **{f"k{index}": index for index in range(MAX_PARAMS + 3)}}

    assert clean_params(params) == {f"k{index}": index for index in range(MAX_PARAMS)}


@pytest.mark.parametrize("value", [None, "types", 5, ["types"], [("types", "Epic")], {}, {"Bad": 1}, {"f": 1.5}])
def test_params_that_are_not_a_mapping_or_hold_nothing_usable_are_empty(value):
    assert clean_params(value) == {}
