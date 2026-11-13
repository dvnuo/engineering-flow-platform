"""Tests for assistant personalization loaded from the agents repository."""

import json
from pathlib import Path

import pytest

from src.gateway.personalization import (
    MAX_CARD_FIELD_CHARS,
    MAX_CARDS,
    MAX_PROMPT_CHARS,
    MAX_WELCOME_CHARS,
    load_personalization,
    personalization_version,
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


# ---------------------------------------------------------------------------
# Inputs that name a source, and cards that are a form (`fields` + `template`).
# ---------------------------------------------------------------------------


def _load_cards(root: Path, cards: list, *, version: int = 1) -> list[dict]:
    # JSON is YAML, and writing the cards as data keeps each case down to the
    # one key it is about.
    _write_portal(root, cards=json.dumps({"cards": cards}))
    return load_personalization(root, version=version)["cards"]


def _legacy_input(root: Path, raw_input: dict) -> dict:
    cards = _load_cards(root, [{"title": "T", "prompt": "Do {{input}}.", "input": raw_input}])
    return cards[0]["input"]


def _form_card(fields, template="Go.", **extra) -> dict:
    return {"title": "Form", "fields": fields, "template": template, **extra}


def _field(root: Path, raw_field: dict) -> dict | None:
    """The field as version 2 emits it, or None when it cost the card its form."""

    cards = _load_cards(root, [_form_card([raw_field])], version=2)
    return cards[0]["fields"][0] if cards else None


def test_input_source_params_and_multiline_are_emitted_when_valid(tmp_path):
    _write_portal(
        tmp_path,
        cards="""
cards:
  - title: Open a pull request
    prompt: |
      Open a pull request for {{input}}.
    input:
      label: Branch
      placeholder: org/repo and branch
      source: github.branches
      params: { types: [Epic] }
      multiline: true
""",
    )

    # No version asked: the new input keys are part of the legacy shape.
    card = load_personalization(tmp_path)["cards"][0]

    assert card["input"] == {
        "label": "Branch",
        "placeholder": "org/repo and branch",
        "source": "github.branches",
        "params": {"types": ["Epic"]},
        "multiline": True,
    }


@pytest.mark.parametrize("source", ["jira", "jira.issues", "a_b.c_1.9d", "a" * 64])
def test_input_source_matching_the_pattern_is_kept(tmp_path, source):
    assert _legacy_input(tmp_path, {"label": "L", "source": source})["source"] == source


@pytest.mark.parametrize(
    "source",
    [
        "GitHub.Branches",
        "github..branches",
        "github.",
        ".branches",
        "9lives",
        "github branches",
        "github-branches",
        " github.branches",
        "github.branches\n",
        "a" * 65,
        "",
        12,
        ["github.branches"],
        None,
    ],
)
def test_input_with_an_invalid_source_stays_a_plain_text_input(tmp_path, source):
    # A bad source is not an error and not a reason to lose the card: the
    # member gets the text box every card had before sources existed.
    assert _legacy_input(tmp_path, {"label": "L", "placeholder": "P", "source": source}) == {
        "label": "L",
        "placeholder": "P",
    }


def test_input_params_keep_only_well_formed_entries(tmp_path):
    params = {
        "project": "  FX  ",
        "long": "x" * 250,
        "open": True,
        "limit": 25,
        "types": [" Epic ", "", "   ", "Story"],
        "Bad-Key": "x",
        "9key": "x",
        "k" * 33: "x",
        "ratio": 1.5,
        "nothing": None,
        "nested": {"a": 1},
        "mixed": ["a", 1],
    }

    assert _legacy_input(tmp_path, {"params": params})["params"] == {
        "project": "FX",
        "long": "x" * 200,
        "open": True,
        "limit": 25,
        "types": ["Epic", "Story"],
    }


def test_input_params_with_a_key_that_is_not_text_lose_that_entry(tmp_path):
    # Only YAML can say this: a JSON key is always a string.
    _write_portal(
        tmp_path,
        cards="cards:\n  - title: T\n    prompt: P\n    input:\n      params: {7: x, false: y, ok: z}\n",
    )

    assert load_personalization(tmp_path)["cards"][0]["input"]["params"] == {"ok": "z"}


def test_input_params_lists_and_entries_are_capped(tmp_path):
    params = {f"k{index}": "v" for index in range(10)}
    params["k0"] = [f"item{index}" for index in range(12)]
    params["k1"] = ["y" * 150]

    cleaned = _legacy_input(tmp_path, {"params": params})["params"]

    assert list(cleaned) == [f"k{index}" for index in range(8)]
    assert cleaned["k0"] == [f"item{index}" for index in range(10)]
    assert cleaned["k1"] == ["y" * 100]


@pytest.mark.parametrize("params", [{}, {"Bad": "x", "f": 1.5}, ["types"], "types", 5, None])
def test_input_params_that_are_empty_or_not_a_mapping_are_omitted(tmp_path, params):
    assert "params" not in _legacy_input(tmp_path, {"source": "jira.issues", "params": params})


@pytest.mark.parametrize("multiline", ["true", "yes", 1, False, None])
def test_input_multiline_needs_the_boolean_true(tmp_path, multiline):
    assert "multiline" not in _legacy_input(tmp_path, {"multiline": multiline})


def test_the_contract_example_form_is_emitted_in_full_at_version_2(tmp_path):
    _write_portal(
        tmp_path,
        cards="""
cards:
  - title: Run mobile scenarios
    description: Pick the story and where to run it.
    icon: smartphone
    fields:
      - name: story
        label: Jira story
        type: text
        placeholder: FX-12
        required: true
        source: mobile.stories
        params: { types: [Story] }
      - name: platforms
        label: Platforms
        type: choice
        multiple: true
        options: [android, ios]
        default: [android, ios]
      - name: csv
        label: CSV file
        type: file
        accept: [".csv"]
        required: false
    template: |
      Run the mobile scenarios of {{story}} on {{platforms}}.
""",
    )

    assert load_personalization(tmp_path, version=2)["cards"] == [
        {
            "title": "Run mobile scenarios",
            "description": "Pick the story and where to run it.",
            "icon": "smartphone",
            "prompt": "",
            "fields": [
                {
                    "name": "story",
                    "label": "Jira story",
                    "type": "text",
                    "placeholder": "FX-12",
                    "required": True,
                    "source": "mobile.stories",
                    "params": {"types": ["Story"]},
                },
                {
                    "name": "platforms",
                    "label": "Platforms",
                    "type": "choice",
                    "placeholder": "",
                    "required": True,
                    "options": [
                        {"value": "android", "label": "android"},
                        {"value": "ios", "label": "ios"},
                    ],
                    "multiple": True,
                    "default": ["android", "ios"],
                },
                {
                    "name": "csv",
                    "label": "CSV file",
                    "type": "file",
                    "placeholder": "",
                    "required": False,
                    "accept": [".csv"],
                },
            ],
            "template": "Run the mobile scenarios of {{story}} on {{platforms}}.",
        }
    ]


def test_version_1_never_emits_fields_or_template(tmp_path):
    card = {
        "title": "Both",
        "prompt": "Do {{input}}.",
        "input": {"label": "L"},
        "fields": [{"name": "story"}],
        "template": "Do {{story}}.",
    }

    for version in (1, 0, -3):
        assert _load_cards(tmp_path, [card], version=version) == [
            {
                "title": "Both",
                "description": "",
                "icon": "sparkles",
                "prompt": "Do {{input}}.",
                "input": {"label": "L", "placeholder": ""},
            }
        ]
    # The default is version 1: a caller that passes nothing is an older Portal.
    assert load_personalization(tmp_path)["cards"] == _load_cards(tmp_path, [card], version=1)


def test_a_card_that_is_only_a_form_is_left_out_at_version_1_and_has_an_empty_prompt_at_version_2(tmp_path):
    # An older Portal has only `prompt` to send; a card with none would be a
    # button that does nothing, so it does not get the card at all.
    card = _form_card([{"name": "story"}], "Do {{story}}.", input={"label": "No prompt to fill"})

    assert _load_cards(tmp_path, [card], version=1) == []
    assert _load_cards(tmp_path, [card], version=2) == [
        {
            "title": "Form",
            "description": "",
            "icon": "sparkles",
            "prompt": "",
            "fields": [
                {"name": "story", "label": "story", "type": "text", "placeholder": "", "required": True}
            ],
            "template": "Do {{story}}.",
        }
    ]


def test_a_card_with_both_forms_carries_both_at_version_2(tmp_path):
    card = {
        "title": "Both",
        "prompt": "Do {{input}}.",
        "input": {"label": "L", "source": "jira.issues"},
        "fields": [{"name": "story"}],
        "template": "Do {{story}}.",
    }

    for version in (2, 3):
        emitted = _load_cards(tmp_path, [card], version=version)[0]
        assert emitted["prompt"] == "Do {{input}}."
        assert emitted["input"] == {"label": "L", "placeholder": "", "source": "jira.issues"}
        assert [field["name"] for field in emitted["fields"]] == ["story"]
        assert emitted["template"] == "Do {{story}}."


def test_both_versions_list_the_same_cards_apart_from_the_form_only_ones(tmp_path):
    cards = []
    for index in range(MAX_CARDS + 4):
        if index % 2:
            cards.append({"title": f"Legacy {index}", "prompt": "P"})
        else:
            cards.append(_form_card([{"name": "a"}], title=f"Form {index}"))

    version_1 = [card["title"] for card in _load_cards(tmp_path, cards, version=1)]
    version_2 = [card["title"] for card in _load_cards(tmp_path, cards, version=2)]

    assert version_2 == [card["title"] for card in cards[:MAX_CARDS]]
    assert version_1 == [title for title in version_2 if title.startswith("Legacy")]


@pytest.mark.parametrize(
    "fields, template",
    [
        pytest.param([{"name": "story"}], "Do {{story}} and {{other}}.", id="undeclared-placeholder"),
        pytest.param([{"name": "story"}], "Do {{ input }}.", id="legacy-placeholder-is-not-a-field"),
        pytest.param([{"name": "a"}, {"name": "a"}], "Do {{a}}.", id="duplicate-name"),
        pytest.param([{"name": f"f{index}"} for index in range(7)], "Go.", id="seven-fields"),
        pytest.param([], "Go.", id="no-field"),
        pytest.param(None, "Go.", id="template-without-fields"),
        pytest.param({"name": "a"}, "Go.", id="fields-is-a-mapping"),
        pytest.param("story", "Go.", id="fields-is-a-string"),
        pytest.param([{"name": "a"}, "b"], "Do {{a}}.", id="entry-is-not-a-mapping"),
        pytest.param([{"label": "No name"}], "Go.", id="name-missing"),
        pytest.param([{"name": "Story"}], "Go.", id="name-upper-case"),
        pytest.param([{"name": "9a"}], "Go.", id="name-starts-with-a-digit"),
        pytest.param([{"name": "a-b"}], "Go.", id="name-with-a-dash"),
        pytest.param([{"name": "a" * 33}], "Go.", id="name-too-long"),
        pytest.param([{"name": 5}], "Go.", id="name-not-text"),
        pytest.param([{"name": "a", "type": "date"}], "Go.", id="unknown-type"),
        pytest.param([{"name": "a", "type": "Text"}], "Go.", id="type-wrong-case"),
        pytest.param([{"name": "a", "type": 3}], "Go.", id="type-not-text"),
        pytest.param([{"name": "a", "type": "choice"}], "Go.", id="choice-without-options"),
        pytest.param([{"name": "a", "type": "choice", "options": []}], "Go.", id="choice-with-empty-options"),
        pytest.param(
            [{"name": "a", "type": "choice", "options": ["", "  ", 5, None, {"label": "No value"}, {"value": ""}]}],
            "Go.",
            id="choice-with-no-valid-option",
        ),
        pytest.param([{"name": "a", "type": "choice", "options": "android"}], "Go.", id="options-is-a-string"),
        pytest.param([{"name": "a"}], None, id="fields-without-template"),
        pytest.param([{"name": "a"}], "", id="empty-template"),
        pytest.param([{"name": "a"}], "   \n", id="blank-template"),
        pytest.param([{"name": "a"}], 5, id="template-not-text"),
    ],
)
def test_an_invalid_form_is_dropped_as_a_pair(tmp_path, fields, template):
    form_only = {"title": "Form only", "fields": fields, "template": template}
    both = {**form_only, "title": "Both", "prompt": "Do it."}

    emitted = _load_cards(tmp_path, [form_only, both], version=2)

    # Without a usable form the first card has nothing to send and goes; the
    # second stands on its legacy prompt and shows no trace of the form.
    assert emitted == [{"title": "Both", "description": "", "icon": "sparkles", "prompt": "Do it."}]


def test_a_minimal_field_gets_the_defaults(tmp_path):
    assert _field(tmp_path, {"name": "story"}) == {
        "name": "story",
        "label": "story",
        "type": "text",
        "placeholder": "",
        "required": True,
    }


def test_six_fields_and_a_32_character_name_are_within_the_limits(tmp_path):
    fields = [{"name": f"f{index}"} for index in range(5)] + [{"name": "n" * 32}]

    emitted = _load_cards(tmp_path, [_form_card(fields)], version=2)[0]["fields"]

    assert [field["name"] for field in emitted] == ["f0", "f1", "f2", "f3", "f4", "n" * 32]


def test_field_label_and_placeholder_are_cleaned_and_capped(tmp_path):
    field = _field(tmp_path, {"name": "a", "label": "  " + "L" * 600, "placeholder": "P" * 600 + "  "})

    assert field["label"] == "L" * MAX_CARD_FIELD_CHARS
    assert field["placeholder"] == "P" * MAX_CARD_FIELD_CHARS
    assert _field(tmp_path, {"name": "a", "label": 5, "placeholder": ["x"]})["label"] == "a"


@pytest.mark.parametrize(
    "raw, expected",
    [(False, False), (True, True), ("no", True), (0, True), (None, True)],
)
def test_field_required_is_a_boolean_and_defaults_to_true(tmp_path, raw, expected):
    assert _field(tmp_path, {"name": "a", "required": raw})["required"] is expected


@pytest.mark.parametrize("field_type", ["textarea", "choice", "file"])
def test_source_and_params_are_kept_only_on_a_text_field(tmp_path, field_type):
    hint = {"source": "jira.issues", "params": {"types": ["Epic"]}}

    text = _field(tmp_path, {"name": "a", **hint})
    other = _field(tmp_path, {"name": "a", "type": field_type, "options": ["x"], **hint})

    assert (text["source"], text["params"]) == ("jira.issues", {"types": ["Epic"]})
    assert other["type"] == field_type
    assert "source" not in other and "params" not in other


def test_a_text_field_with_an_invalid_source_keeps_the_field(tmp_path):
    # Unlike an unknown type, a bad source costs only itself: the field is
    # still a text box the template can use.
    field = _field(tmp_path, {"name": "a", "source": "Jira Issues", "params": {"Bad": 1, "f": 1.5}})

    assert field == {"name": "a", "label": "a", "type": "text", "placeholder": "", "required": True}


def test_choice_options_accept_strings_and_value_label_mappings(tmp_path):
    field = _field(
        tmp_path,
        {
            "name": "a",
            "type": "choice",
            "options": [
                " android ",
                {"value": "ios", "label": "iOS"},
                {"value": "web"},
                {"value": "tv", "label": 5},
                "",
                7,
                {"label": "No value"},
                "v" * 250,
                {"value": "long", "label": "l" * 250},
            ],
        },
    )

    assert field["options"] == [
        {"value": "android", "label": "android"},
        {"value": "ios", "label": "iOS"},
        {"value": "web", "label": "web"},
        {"value": "tv", "label": "tv"},
        {"value": "v" * 200, "label": "v" * 200},
        {"value": "long", "label": "l" * 200},
    ]
    assert field["multiple"] is False


def test_choice_options_are_capped_at_twenty(tmp_path):
    field = _field(tmp_path, {"name": "a", "type": "choice", "options": [f"o{index}" for index in range(25)]})

    assert [option["value"] for option in field["options"]] == [f"o{index}" for index in range(20)]


@pytest.mark.parametrize("raw, expected", [(True, True), (False, False), ("yes", False), (1, False), (None, False)])
def test_choice_multiple_is_a_boolean_and_defaults_to_false(tmp_path, raw, expected):
    field = _field(tmp_path, {"name": "a", "type": "choice", "options": ["x"], "multiple": raw})

    assert field["multiple"] is expected


@pytest.mark.parametrize("field_type", ["text", "textarea", "file"])
def test_options_and_multiple_belong_to_a_choice_only(tmp_path, field_type):
    field = _field(tmp_path, {"name": "a", "type": field_type, "options": ["x"], "multiple": True})

    assert "options" not in field and "multiple" not in field


@pytest.mark.parametrize(
    "multiple, default, expected",
    [
        (True, ["android", "windows", "ios"], ["android", "ios"]),
        (True, "ios", "ios"),
        (True, ["windows"], None),
        (True, "windows", None),
        (False, ["windows", "ios", "android"], ["ios"]),
        (False, "android", "android"),
        (False, " android ", "android"),
        (False, "windows", None),
        (False, [], None),
        (False, 5, None),
    ],
)
def test_choice_defaults_are_filtered_to_the_option_values(tmp_path, multiple, default, expected):
    field = _field(
        tmp_path,
        {"name": "a", "type": "choice", "options": ["android", "ios"], "multiple": multiple, "default": default},
    )

    # An empty default is left out rather than sent as "" or [].
    assert field.get("default") == expected
    assert ("default" in field) is (expected is not None)


@pytest.mark.parametrize(
    "default, expected",
    [
        ("  FX-12 ", "FX-12"),
        (["a", " b ", "", 5, None], ["a", "b"]),
        ([f"d{index}" for index in range(25)], [f"d{index}" for index in range(20)]),
        ("   ", None),
        ([], None),
        (["", "  "], None),
        (5, None),
        ({"value": "x"}, None),
    ],
)
def test_text_default_is_a_string_or_a_capped_list_and_omitted_when_empty(tmp_path, default, expected):
    field = _field(tmp_path, {"name": "a", "default": default})

    assert field.get("default") == expected
    assert ("default" in field) is (expected is not None)


def test_file_accept_keeps_lower_cased_extensions_only(tmp_path):
    field = _field(
        tmp_path,
        {
            "name": "a",
            "type": "file",
            "accept": [".CSV", ".xlsx", "csv", ".tar.gz", ".", "." + "x" * 11, "*.csv", 5, ".x1"],
        },
    )

    assert field["accept"] == [".csv", ".xlsx", ".x1"]


def test_file_accept_is_capped_and_omitted_when_empty(tmp_path):
    many = _field(tmp_path, {"name": "a", "type": "file", "accept": [f".e{index}" for index in range(12)]})
    none = _field(tmp_path, {"name": "a", "type": "file", "accept": ["csv", 5]})
    not_a_list = _field(tmp_path, {"name": "a", "type": "file", "accept": ".csv"})
    on_text = _field(tmp_path, {"name": "a", "accept": [".csv"]})

    assert many["accept"] == [f".e{index}" for index in range(8)]
    assert "accept" not in none and "accept" not in not_a_list and "accept" not in on_text


def test_template_is_capped_and_may_leave_a_field_unused(tmp_path):
    fields = [{"name": "story"}, {"name": "csv", "type": "file"}]
    card = _form_card(fields, "  Run {{ story }} now. " + "x" * (MAX_PROMPT_CHARS + 500))

    emitted = _load_cards(tmp_path, [card], version=2)[0]

    # `csv` is attached rather than quoted, so no placeholder names it, and
    # spaces inside the braces are part of the placeholder syntax.
    assert [field["name"] for field in emitted["fields"]] == ["story", "csv"]
    assert emitted["template"].startswith("Run {{ story }} now. x")
    assert len(emitted["template"]) == MAX_PROMPT_CHARS


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, 1),
        ("", 1),
        ("1", 1),
        ("0", 1),
        ("2", 2),
        (" 2 ", 2),
        ("02", 2),
        ("3", 2),
        ("17", 2),
        ("-2", 1),
        ("2.0", 1),
        ("v2", 1),
        ("two", 1),
        ("true", 1),
        (True, 1),
        (2, 2),
    ],
)
def test_only_a_whole_number_from_2_up_asks_for_the_form(raw, expected):
    assert personalization_version(raw) == expected


@pytest.mark.asyncio
async def test_the_endpoint_serves_the_form_only_to_a_caller_that_asks_for_it(tmp_path, monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    from src.gateway import runtime_api

    _write_portal(
        tmp_path,
        welcome="Hello.",
        cards=json.dumps(
            {
                "cards": [
                    {"title": "Legacy", "prompt": "Do {{input}}.", "input": {"source": "jira.issues"}},
                    _form_card([{"name": "story"}], "Do {{story}}."),
                ]
            }
        ),
    )
    monkeypatch.setattr(runtime_api, "resolve_runtime_workspace", lambda _config: tmp_path)

    async def get(query: str) -> dict:
        request = make_mocked_request("GET", "/api/personalization" + query)
        response = await runtime_api.api_personalization(request)
        assert response.status == 200
        return json.loads(response.text)

    for query in ("", "?v=1", "?v=abc", "?v=", "?version=2"):
        payload = await get(query)
        assert payload["welcome"] == "Hello."
        assert [card["title"] for card in payload["cards"]] == ["Legacy"]
        assert payload["cards"][0]["input"]["source"] == "jira.issues"
        assert all("fields" not in card and "template" not in card for card in payload["cards"])

    payload = await get("?v=2")
    assert [card["title"] for card in payload["cards"]] == ["Legacy", "Form"]
    assert payload["cards"][1]["prompt"] == ""
    assert payload["cards"][1]["template"] == "Do {{story}}."
    assert "fields" not in payload["cards"][0]


def test_the_runtime_contract_documents_the_card_shapes():
    text = Path("docs/runtime_contract.md").read_text(encoding="utf-8")

    for needle in ["`GET /api/personalization`", "`?v=2`", "`fields`", "`template`", "`source`", "`multiline`"]:
        assert needle in text
