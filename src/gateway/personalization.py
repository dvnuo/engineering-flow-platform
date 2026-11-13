"""Assistant personalization loaded from the agents repository.

The Portal clones the agents repo into the workspace at boot, so the branch an
admin picked for an assistant type is already on disk. Reading it here — rather
than having Portal clone it a second time — keeps one source of truth and
guarantees what a member sees matches the branch the assistant actually booted
with.

Expected layout at the workspace root, alongside the existing AGENTS.md and
instructions/ that the personalization contract already defines:

    portal/welcome.md    greeting shown when a chat has no messages yet
    portal/cards.yaml    starter prompts offered as clickable cards

Both files are optional. A branch without them simply has no personalization,
and Portal falls back to its generic welcome.

A card has two forms. The legacy one is a ``prompt`` with at most one value,
``{{input}}``. The second is a small form: ``fields`` plus the ``template``
their values are placed in. A Portal that asks for neither version gets only
the legacy shape, so a Portal older than the form keeps rendering what it
understands and never a card it cannot fill in.
"""
from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
import re
from typing import Any

from ruamel.yaml import YAML

from src.efp_runtime.input_sources import clean_params, clean_source, is_name, is_sequence

logger = logging.getLogger(__name__)

# The repository declares ruamel.yaml, not PyYAML. Importing `yaml` here raised
# ModuleNotFoundError in a clean container, which the broad handler below then
# swallowed -- every valid cards.yaml became an empty list and the feature was
# silently dead outside development.
_yaml = YAML(typ="safe")

PORTAL_PERSONALIZATION_DIRNAME = "portal"
WELCOME_FILENAME = "welcome.md"
CARDS_FILENAME = "cards.yaml"

# A greeting is prose, not a document, and cards are a short menu. Both are
# capped so a mistaken commit in the agents repo degrades the panel instead of
# flooding the chat pane.
MAX_WELCOME_CHARS = 4000
MAX_CARDS = 12
MAX_CARD_FIELD_CHARS = 500
MAX_PROMPT_CHARS = 4000

# The response shape a caller that names no version gets, and the first one
# that carries the form (`fields` + `template`).
LEGACY_VERSION = 1
FIELDS_VERSION = 2

# A form is something a member fills in before a first message, so it stays
# small. Unlike the text caps above, too many fields is not cut down to size:
# a template that names a field which was cut would be sent with a hole in it.
MAX_FIELDS = 6
FIELD_TYPES = ("text", "textarea", "choice", "file")
DEFAULT_FIELD_TYPE = "text"
MAX_OPTIONS = 20
MAX_OPTION_CHARS = 200
MAX_DEFAULTS = 20
MAX_ACCEPT = 8

_ACCEPT_PATTERN = re.compile(r"\.[A-Za-z0-9]{1,10}")
_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


def personalization_version(raw: Any) -> int:
    """The response version a `v` query value asks for.

    Anything that is not a whole number of 2 or more means the legacy shape,
    a missing value included: the parameter is how a Portal says it can render
    the form, and a Portal that says nothing must not be sent one.
    """

    text = str(raw).strip() if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
    if not re.fullmatch(r"[0-9]{1,9}", text):
        return LEGACY_VERSION
    return FIELDS_VERSION if int(text) >= FIELDS_VERSION else LEGACY_VERSION


def load_personalization(workspace_root: Path, *, version: int = LEGACY_VERSION) -> dict[str, Any]:
    """Return {"welcome": str|None, "cards": list} for one workspace.

    ``version`` is the card shape the caller can render: below 2 every card is
    the legacy shape and a card that is only a form is left out; from 2 a card
    also carries its valid ``fields`` and ``template``.
    """

    portal_dir = Path(workspace_root) / PORTAL_PERSONALIZATION_DIRNAME
    return {
        "welcome": _load_welcome(portal_dir / WELCOME_FILENAME),
        "cards": _load_cards(portal_dir / CARDS_FILENAME, version=version),
    }


def _load_welcome(path: Path) -> str | None:
    text = _read_text(path)
    if not text:
        return None
    return text[:MAX_WELCOME_CHARS]


def _load_cards(path: Path, *, version: int = LEGACY_VERSION) -> list[dict[str, Any]]:
    text = _read_text(path)
    if not text:
        return []
    try:
        parsed = _yaml.load(text)
    except Exception:
        logger.warning("Ignoring unreadable personalization cards at %s", path, exc_info=True)
        return []

    # ruamel returns a CommentedMap, which is a Mapping but not necessarily
    # caught by an isinstance(dict) check depending on the loader.
    raw_cards = parsed.get("cards") if isinstance(parsed, Mapping) else parsed
    if not is_sequence(raw_cards):
        return []

    # The cap counts entries of the file, whatever the version, so the cards
    # of both versions are the same ones: version 1 is version 2 without the
    # cards that are only a form.
    cards: list[dict[str, Any]] = []
    for raw in list(raw_cards)[:MAX_CARDS]:
        card = _normalize_card(raw, version=version)
        if card is not None:
            cards.append(card)
    return cards


def _normalize_card(raw: Any, *, version: int = LEGACY_VERSION) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    title = _clean(raw.get("title"), MAX_CARD_FIELD_CHARS)
    prompt = _clean(raw.get("prompt"), MAX_PROMPT_CHARS)
    form = _normalize_form(raw) if version >= FIELDS_VERSION else None
    # A card with no title has nothing to click; one with no prompt does
    # nothing when clicked. Either way it is not worth rendering. A form is a
    # prompt only for a caller that asked for it.
    if not title or not (prompt or form):
        return None

    card: dict[str, Any] = {
        "title": title,
        "description": _clean(raw.get("description"), MAX_CARD_FIELD_CHARS),
        "icon": _clean(raw.get("icon"), 64) or "sparkles",
        "prompt": prompt,
    }

    # `input` is the one value of the legacy prompt; without that prompt there
    # is nothing to put it in.
    raw_input = raw.get("input")
    if prompt and isinstance(raw_input, Mapping):
        card["input"] = _normalize_input(raw_input)
    if form is not None:
        card["fields"], card["template"] = form
    return card


def _normalize_input(raw_input: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "label": _clean(raw_input.get("label"), MAX_CARD_FIELD_CHARS) or "Details",
        "placeholder": _clean(raw_input.get("placeholder"), MAX_CARD_FIELD_CHARS),
    }
    # The keys below are emitted only when they hold something, so a card that
    # does not use them serializes exactly as it did before they existed.
    source = clean_source(raw_input.get("source"))
    if source:
        normalized["source"] = source
    params = clean_params(raw_input.get("params"))
    if params:
        normalized["params"] = params
    if raw_input.get("multiline") is True:
        normalized["multiline"] = True
    return normalized


def _normalize_form(raw: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str] | None:
    """The card's (fields, template), or None when the pair is not usable.

    The two are valid together or not at all. A field left out of an otherwise
    valid form would send the member a prompt with a gap where its value
    belonged, so one bad field drops the form rather than itself; the card then
    stands on its legacy prompt, or is dropped when it has none.
    """

    raw_fields = raw.get("fields")
    template = _clean(raw.get("template"), MAX_PROMPT_CHARS)
    if not template or not is_sequence(raw_fields) or not 1 <= len(raw_fields) <= MAX_FIELDS:
        return None

    fields: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw_field in raw_fields:
        field = _normalize_field(raw_field)
        if field is None or field["name"] in names:
            return None
        names.add(field["name"])
        fields.append(field)

    # A field no placeholder uses is fine (a file is attached, not quoted); a
    # placeholder no field fills is not.
    if not set(_TEMPLATE_PLACEHOLDER_PATTERN.findall(template)) <= names:
        return None
    return fields, template


def _normalize_field(raw: Any) -> dict[str, Any] | None:
    """One form field, or None when it invalidates the form."""

    if not isinstance(raw, Mapping):
        return None
    name = raw.get("name")
    if not is_name(name):
        return None
    raw_type = raw.get("type")
    field_type = DEFAULT_FIELD_TYPE if raw_type is None else raw_type
    if not isinstance(field_type, str) or field_type not in FIELD_TYPES:
        return None

    raw_required = raw.get("required")
    field: dict[str, Any] = {
        "name": name,
        "label": _clean(raw.get("label"), MAX_CARD_FIELD_CHARS) or name,
        "type": field_type,
        "placeholder": _clean(raw.get("placeholder"), MAX_CARD_FIELD_CHARS),
        "required": raw_required if isinstance(raw_required, bool) else True,
    }

    options: list[dict[str, str]] = []
    if field_type == "text":
        # A picker fills one line of text; the other types have their own
        # control, so a source on them would be a promise nothing keeps.
        source = clean_source(raw.get("source"))
        if source:
            field["source"] = source
        params = clean_params(raw.get("params"))
        if params:
            field["params"] = params
    elif field_type == "choice":
        options = _normalize_options(raw.get("options"))
        if not options:
            return None
        field["options"] = options
        field["multiple"] = raw.get("multiple") is True
    elif field_type == "file":
        accept = _normalize_accept(raw.get("accept"))
        if accept:
            field["accept"] = accept

    default = _normalize_default(raw.get("default"), field, options)
    if default:
        field["default"] = default
    return field


def _normalize_options(raw: Any) -> list[dict[str, str]]:
    if not is_sequence(raw):
        return []
    options: list[dict[str, str]] = []
    for item in raw:
        if len(options) >= MAX_OPTIONS:
            break
        if isinstance(item, Mapping):
            value = _clean(item.get("value"), MAX_OPTION_CHARS)
            label = _clean(item.get("label"), MAX_OPTION_CHARS) or value
        else:
            value = label = _clean(item, MAX_OPTION_CHARS)
        if value:
            options.append({"value": value, "label": label})
    return options


def _normalize_accept(raw: Any) -> list[str]:
    if not is_sequence(raw):
        return []
    accept = [
        item.lower()
        for item in raw
        if isinstance(item, str) and _ACCEPT_PATTERN.fullmatch(item) is not None
    ]
    return accept[:MAX_ACCEPT]


def _normalize_default(
    raw: Any,
    field: Mapping[str, Any],
    options: list[dict[str, str]],
) -> str | list[str]:
    """The field's default in the shape it was written, "" or [] when there is none.

    A string stays a string and a list stays a list: the Portal reads both, and
    which of the two a field wants is its business. A choice keeps only the
    defaults that are among its options, since a default the member cannot see
    selected would be sent without their knowing.
    """

    is_choice = field["type"] == "choice"
    limit = MAX_OPTION_CHARS if is_choice else MAX_CARD_FIELD_CHARS
    if isinstance(raw, str):
        values = [_clean(raw, limit)]
    elif is_sequence(raw):
        values = [_clean(item, limit) for item in raw]
    else:
        return ""
    values = [value for value in values if value][:MAX_DEFAULTS]
    if is_choice:
        allowed = {option["value"] for option in options}
        values = [value for value in values if value in allowed]
        if not field["multiple"]:
            values = values[:1]
    if isinstance(raw, str):
        return values[0] if values else ""
    return values


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _read_text(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.debug("Could not read personalization file %s", path, exc_info=True)
        return ""
