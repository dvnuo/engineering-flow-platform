"""Tests for assistant personalization loaded from the agents repository."""

from pathlib import Path

import pytest

from src.gateway.personalization import (
    MAX_CARDS,
    MAX_WELCOME_CHARS,
    load_personalization,
)


def _write_portal(root: Path, *, welcome: str | None = None, cards: str | None = None) -> None:
    portal = root / "portal"
    portal.mkdir(parents=True, exist_ok=True)
    if welcome is not None:
        (portal / "welcome.md").write_text(welcome, encoding="utf-8")
    if cards is not None:
        (portal / "cards.yaml").write_text(cards, encoding="utf-8")


def test_cards_are_parsed_with_the_declared_yaml_library(tmp_path):
    # The repository declares ruamel.yaml, not PyYAML. `import yaml` raised
    # ModuleNotFoundError in a clean container, and the broad handler in
    # _load_cards swallowed it -- every cards.yaml silently became empty.
    source = Path("src/gateway/personalization.py").read_text(encoding="utf-8")

    assert "import yaml" not in source
    assert "from ruamel.yaml import YAML" in source

    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "ruamel.yaml" in requirements
    assert "pyyaml" not in requirements, (
        "if PyYAML is ever declared, say so here rather than relying on it "
        "being pulled in transitively"
    )


def test_a_commented_map_from_ruamel_is_accepted(tmp_path):
    # ruamel returns CommentedMap rather than dict, so an isinstance(dict)
    # check would drop every card.
    _write_portal(
        tmp_path,
        cards="cards:\n  - title: T   # trailing comment\n    prompt: P\n",
    )

    assert [card["title"] for card in load_personalization(tmp_path)["cards"]] == ["T"]


def test_missing_portal_directory_yields_empty_personalization(tmp_path):
    result = load_personalization(tmp_path)
    assert result == {"welcome": None, "cards": []}


def test_welcome_and_cards_are_loaded(tmp_path):
    _write_portal(
        tmp_path,
        welcome="Hello there.",
        cards="""
cards:
  - title: Draft test cases
    description: From a ticket.
    icon: clipboard-check
    input:
      label: Ticket
      placeholder: ABC-1
    prompt: |
      Design test cases for {{input}}.
""",
    )

    result = load_personalization(tmp_path)

    assert result["welcome"] == "Hello there."
    assert len(result["cards"]) == 1
    card = result["cards"][0]
    assert card["title"] == "Draft test cases"
    assert card["icon"] == "clipboard-check"
    assert card["input"] == {"label": "Ticket", "placeholder": "ABC-1"}
    assert "{{input}}" in card["prompt"]


def test_card_without_title_or_prompt_is_dropped(tmp_path):
    # A card with no title has nothing to click; one with no prompt does nothing
    # when clicked. Neither should reach the UI.
    _write_portal(
        tmp_path,
        cards="""
cards:
  - description: no title here
    prompt: do something
  - title: no prompt here
    description: nothing happens
  - title: Valid
    prompt: go
""",
    )

    cards = load_personalization(tmp_path)["cards"]

    assert [card["title"] for card in cards] == ["Valid"]


def test_card_without_icon_gets_a_default(tmp_path):
    _write_portal(tmp_path, cards="cards:\n  - title: T\n    prompt: P\n")

    assert load_personalization(tmp_path)["cards"][0]["icon"] == "sparkles"


def test_malformed_yaml_is_ignored_rather_than_raising(tmp_path):
    # A bad commit in the agents repo must degrade the panel, never break chat.
    _write_portal(tmp_path, welcome="Still here.", cards="cards: [ unclosed")

    result = load_personalization(tmp_path)

    assert result["welcome"] == "Still here."
    assert result["cards"] == []


def test_bare_list_without_cards_key_is_accepted(tmp_path):
    _write_portal(tmp_path, cards="- title: T\n  prompt: P\n")

    assert [card["title"] for card in load_personalization(tmp_path)["cards"]] == ["T"]


def test_oversized_content_is_capped(tmp_path):
    long_welcome = "x" * (MAX_WELCOME_CHARS + 500)
    many_cards = "cards:\n" + "".join(
        f"  - title: T{index}\n    prompt: P{index}\n" for index in range(MAX_CARDS + 5)
    )
    _write_portal(tmp_path, welcome=long_welcome, cards=many_cards)

    result = load_personalization(tmp_path)

    assert len(result["welcome"]) == MAX_WELCOME_CHARS
    assert len(result["cards"]) == MAX_CARDS


@pytest.mark.parametrize("payload", ["", "   \n", "cards:\n"])
def test_empty_or_valueless_cards_file_yields_no_cards(tmp_path, payload):
    _write_portal(tmp_path, cards=payload)

    assert load_personalization(tmp_path)["cards"] == []
