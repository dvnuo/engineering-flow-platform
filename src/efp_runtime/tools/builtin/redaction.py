"""Secret redaction for built-in tool output.

The ``bash`` tool hands raw process output to the model and archives it under
the workspace. Troubleshooting CLIs (aws, kubectl, pgsql, ...) can print
credentials, tokens, or private keys, so both copies pass through this filter
first. It is deliberately pattern-based and self-contained: the runtime
package must not import ``src.utils`` (see tests/runtime/test_import_boundary),
and a false positive costs a ``***REDACTED***`` in an otherwise readable
output, while a miss leaks a secret into the transcript and the workspace.

Patterns alone cannot catch a bare value on a line of its own -- what
``aws configure get``, ``echo $PGPASSWORD`` or a jsonpath query prints. For the
credentials this runtime itself injects, the values are known: every
secret-looking variable in the environment is replaced literally as well, which
is the only reliable defence for that shape.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "***REDACTED***"

# The private-key body is bounded: an unbounded lazy `[\s\S]*?` between BEGIN
# and END rescans to end-of-text from every BEGIN, which is quadratic and turns
# a few thousand unterminated markers into minutes of blocked event loop. A PEM
# key is far below this bound. `PRIVATE KEY BLOCK` covers the PGP spelling.
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?:[A-Z ]{0,40})PRIVATE KEY(?: BLOCK)?-----[\s\S]{0,8000}?"
    r"-----END (?:[A-Z ]{0,40})PRIVATE KEY(?: BLOCK)?-----",
    re.IGNORECASE,
)
# A BEGIN with no END is redacted to the end of the text: the process may have
# been killed mid-read, and half a key is still a key.
_PRIVATE_KEY_TAIL_RE = re.compile(
    r"-----BEGIN (?:[A-Z ]{0,40})PRIVATE KEY(?: BLOCK)?-----[\s\S]*\Z",
    re.IGNORECASE,
)

# Secret-looking key names. Matched as a substring of the key so that a prefixed
# or camel-cased name is caught too: EFP_PGSQL_INSTANCES_0_PASSWORD,
# "SecretAccessKey", "clientSecret", db-password. This mirrors
# sensitiveFieldPattern in the tools repo (internal/output/redact.go).
_SECRET_KEY_NAMES = (
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "token",
    "sessionkey",
    "session_key",
    "api_key",
    "apikey",
    "secret",
    "credential",
    "private_key",
    "privatekey",
    "auth",
)
_KEY_GROUP = "|".join(re.escape(name) for name in _SECRET_KEY_NAMES)
# What may sit either side of the name inside one key: EFP_AWS_, aws_, x-, db-,
# and the camel-case tail of "SecretAccessKey".
_KEY_PART = r"[A-Za-z0-9_.\-]*"
_KEY_RE = _KEY_PART + r"(?:" + _KEY_GROUP + r")" + _KEY_PART

# A value that says nothing about a secret: redacting `automountServiceAccountToken: true`
# or `"token": null` hides a setting the reader needs and protects nothing.
_NOT_A_SECRET = r"(?!(?:true|false|null|none)\b)"

_TEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|splunk|token)\s+)([^\s,;\"']+)"), r"\1" + REDACTED),
    # Authorization with no scheme word, and the Splunk style headers.
    (re.compile(r"(?i)((?:authorization|x-api-key|x-auth-token|cookie|set-cookie)\s*:\s*)([^\n\r]+)"), r"\1" + REDACTED),
    # "password": "value" / 'token': 'value' (JSON and YAML flow style)
    (re.compile(r'(?i)(["\'](?:' + _KEY_RE + r')["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    # "password": 12345 -- an unquoted JSON scalar. Not an object or array
    # opening ("certificateAuthority": {), which is structure, not a value.
    (re.compile(r'(?i)(["\'](?:' + _KEY_RE + r')["\']\s*:\s*)' + _NOT_A_SECRET + r'([^\s,}\]"\'{\[]+)'), r"\1" + REDACTED),
    # <password>value</password> (XML, and the Splunk /services/auth/login body)
    (re.compile(r"(?i)(<\s*(" + _KEY_RE + r")\s*>)([^<]*)(<\s*/\s*\2\s*>)"), r"\1" + REDACTED + r"\4"),
    # password=value / token: value / EFP_PGSQL_INSTANCES_0_PASSWORD=value
    (re.compile(r"(?i)(\b(?:" + _KEY_RE + r")\s*[=:]\s*)" + _NOT_A_SECRET + r"([^\s&\"',;]+)"), r"\1" + REDACTED),
    # --password value / -p value / --token value, space separated
    (re.compile(r"(?i)(--(?:" + _KEY_RE + r")[= ])([^\s\"';|&]+)"), r"\1" + REDACTED),
    # curl -u user:pass, splunk -auth user:pass
    (re.compile(r"(?i)((?:^|\s)(?:-u|--user|-auth|--auth)\s+)([^\s:\"']+):([^\s\"';|&]+)"), r"\1\2:" + REDACTED),
    (re.compile(r"(?i)(\b(?:pgpassword|ad_pass|saml2aws_password|gh_token|github_token|gh_enterprise_token)\s*=\s*)([^\s\"',;]+)"), r"\1" + REDACTED),
    # host:port:database:user:password -- a .pgpass line. The port field must
    # be a number or *, which is what tells a .pgpass line from the other
    # colon-separated lines a troubleshooting session prints on their own:
    # ARNs (arn:aws:eks:eu-west-1:111111111111:cluster/x) and compact JSON.
    (re.compile(r"(?m)^([^\s:]+:(?:\d+|\*):[^\s:]*:[^\s:]*:)(\S+)$"), r"\1" + REDACTED),
    # Well-known token shapes.
    (re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bxox[bpa]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), REDACTED),
]

_URL_CREDS_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+", re.IGNORECASE)

# Environment variable names whose value is a secret this runtime injected.
_SECRET_ENV_NAME_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api_key|apikey|passphrase|credential|ad_pass)"
)
# Below this length a "secret" is more likely to be a word that appears
# everywhere ("true", "none"), and blanking it would make output unreadable.
_MIN_LITERAL_SECRET_LEN = 6

_literal_cache: tuple[int, tuple[str, ...]] | None = None


def _configured_secret_values() -> tuple[str, ...]:
    """Return the secret values this process carries in its environment.

    The bash child inherits the runtime's environment, so ``env``, ``printenv``
    or a CLI that dumps its configuration on failure can print any of them with
    no recognisable key next to it. Those values are known here, so they are
    replaced literally, longest first so a value that contains another is not
    left half-redacted.
    """

    global _literal_cache
    env = os.environ
    # Cheap staleness check: the runtime sets these once at boot.
    key = len(env)
    cached = _literal_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    values: set[str] = set()
    for name, value in env.items():
        if not value or len(value) < _MIN_LITERAL_SECRET_LEN:
            continue
        if _SECRET_ENV_NAME_RE.search(name):
            values.add(value)
    ordered = tuple(sorted(values, key=len, reverse=True))
    _literal_cache = (key, ordered)
    return ordered


def _sanitize_url_credentials(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw
        if parsed.username is None or parsed.hostname is None:
            return raw
        netloc = f"{parsed.username}:{REDACTED}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    return _URL_CREDS_RE.sub(_replace, text)


def redact_tool_output(text: str | None) -> str:
    """Return ``text`` with embedded credentials replaced by ``***REDACTED***``."""

    if not text:
        return ""
    value = str(text)
    value = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, value)
    value = _PRIVATE_KEY_TAIL_RE.sub(REDACTED, value)
    value = _sanitize_url_credentials(value)
    for pattern, replacement in _TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    for secret in _configured_secret_values():
        if secret in value:
            value = value.replace(secret, REDACTED)
    return value
