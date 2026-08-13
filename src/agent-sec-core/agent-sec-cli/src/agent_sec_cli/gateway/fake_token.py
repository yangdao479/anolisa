"""Placeholder credential generation, shaped after the real credential.

The agent must believe it holds a usable credential, otherwise it never issues
the request the gateway is supposed to rewrite. So the placeholder has to look
like the real thing -- and "the real thing" differs per provider: ``ghp_`` + 36
base62 for a GitHub PAT, ``sk-``/``sk-ant-`` for OpenAI/Anthropic, ``AIza`` for
Google, ``xoxb-`` for Slack, plus every internal service with its own scheme.

Rather than hardcode a provider table (which would be a standing maintenance
burden and silently wrong whenever a provider changes format), the shape is
**derived from the real credential**: same length, same prefix, same character
class. That is correct by construction for any provider, including ones this
code has never heard of.

A recognisable marker is embedded so a value found in a log, a commit or an
agent transcript is immediately identifiable as inert.
"""

import secrets
import string

# Embedded in generated placeholders so an operator can tell at a glance that a
# leaked value was never live.
FAKE_MARKER = "AGENTSEC"

_BASE62 = string.ascii_letters + string.digits
_HEX_LOWER = string.digits + "abcdef"
_HEX_UPPER = string.digits + "ABCDEF"

# Separators that conventionally end a credential's scheme prefix, e.g. the "_"
# in "ghp_" or the "-" in "sk-ant-".
_PREFIX_SEPARATORS = "_-"
# Only look for a prefix near the start; a separator deep inside the random body
# is not a scheme marker.
_MAX_PREFIX_SCAN = 16
# Below this, there is not enough room for both randomness and a marker.
_MIN_BODY_FOR_MARKER = 12

# GitHub classic PAT, used as the default shape when no real credential is at
# hand (test fixtures). Verified against the format the gateway is tested with.
GITHUB_PAT_PREFIX = "ghp_"
GITHUB_PAT_BODY_LENGTH = 36
GITHUB_PAT_TOTAL_LENGTH = len(GITHUB_PAT_PREFIX) + GITHUB_PAT_BODY_LENGTH


def split_prefix(token: str) -> tuple[str, str]:
    """Split *token* into its scheme prefix and its body.

    The prefix is everything up to and including the last separator that occurs
    near the start, so ``sk-ant-api03-xxx`` yields ``("sk-ant-api03-", "xxx")``
    and a token with no separator yields ``("", token)``.
    """
    scan_limit = min(len(token), _MAX_PREFIX_SCAN)
    cut = -1
    for index in range(scan_limit):
        if token[index] in _PREFIX_SEPARATORS:
            cut = index
    if cut < 0:
        return "", token
    return token[: cut + 1], token[cut + 1 :]


def _charset_for(body: str) -> str:
    """Return the character set to draw from, inferred from *body*."""
    if body and all(char in _HEX_LOWER for char in body):
        return _HEX_LOWER
    if body and all(char in _HEX_UPPER for char in body):
        return _HEX_UPPER
    return _BASE62


def _marker_for(charset: str, marker: str) -> str:
    """Return a marker expressible in *charset*, or "" when none is."""
    if all(char in charset for char in marker):
        return marker
    # A hex-only credential cannot carry "AGENTSEC"; "AE" keeps a hint of it
    # while staying valid hex.
    hex_marker = "".join(char for char in marker.upper() if char in "ABCDEF")
    if hex_marker and all(char in charset for char in hex_marker):
        return hex_marker
    return ""


def generate_fake_token_like(
    real_token: str,
    marker: str = FAKE_MARKER,
    keep_prefix: int | None = None,
) -> str:
    """Return a placeholder with the same shape as *real_token*.

    Same total length, same scheme prefix and same character class, with
    *marker* embedded at the end of the body when it fits.

    The prefix is detected from separators (``ghp_``, ``sk-ant-``, ``xoxb-``).
    Providers whose prefix has **no** separator -- Google's ``AIza...`` is the
    common case -- are not auto-detected; pass *keep_prefix* to preserve a fixed
    number of leading characters verbatim (``keep_prefix=4`` for ``AIza``).

    # Errors
    Raises ``ValueError`` when *real_token* is empty or *keep_prefix* is not a
    valid offset into it.
    """
    real_token = real_token.strip()
    if not real_token:
        raise ValueError("real_token must not be empty")

    if keep_prefix is None:
        prefix, body = split_prefix(real_token)
    else:
        if not 0 <= keep_prefix < len(real_token):
            raise ValueError(
                f"keep_prefix must be within [0, {len(real_token)}), "
                f"got {keep_prefix}"
            )
        prefix, body = real_token[:keep_prefix], real_token[keep_prefix:]

    charset = _charset_for(body)
    usable_marker = _marker_for(charset, marker)
    if len(body) < _MIN_BODY_FOR_MARKER:
        usable_marker = ""

    random_length = len(body) - len(usable_marker)
    random_part = "".join(secrets.choice(charset) for _ in range(random_length))
    return f"{prefix}{random_part}{usable_marker}"


def generate_github_fake_token(marker: str = FAKE_MARKER) -> str:
    """Return a placeholder shaped like a GitHub classic PAT.

    For fixtures and tests where no real credential is available. Prefer
    :func:`generate_fake_token_like` whenever the real credential is known,
    because it matches whatever provider is actually in play.

    # Errors
    Raises ``ValueError`` when *marker* is not base62 or leaves no room for
    randomness.
    """
    if not marker:
        raise ValueError("marker must not be empty")
    if any(char not in _BASE62 for char in marker):
        raise ValueError("marker must be base62 to keep the PAT shape valid")

    random_length = GITHUB_PAT_BODY_LENGTH - len(marker)
    if random_length < 8:
        raise ValueError(
            f"marker too long: leaves only {random_length} random characters"
        )

    body = "".join(secrets.choice(_BASE62) for _ in range(random_length))
    return f"{GITHUB_PAT_PREFIX}{body}{marker}"


def is_github_pat_shaped(token: str) -> bool:
    """Return whether *token* has the length and prefix of a GitHub PAT."""
    return (
        len(token) == GITHUB_PAT_TOTAL_LENGTH
        and token.startswith(GITHUB_PAT_PREFIX)
        and all(char in _BASE62 for char in token[len(GITHUB_PAT_PREFIX) :])
    )


def same_shape(left: str, right: str) -> bool:
    """Return whether two credentials share prefix, length and character class.

    Used to sanity-check a hand-written ``fake_token`` against its
    ``real_token``: a mismatch is what makes a client reject the placeholder
    before the gateway ever sees the request.

    Only checks what is mechanically detectable -- length, separator-delimited
    prefix and character class. It cannot know that ``AIza`` is meaningful to
    Google, so a separator-less provider prefix will compare equal even when it
    differs; verify those by eye.
    """
    if len(left) != len(right):
        return False
    left_prefix, left_body = split_prefix(left)
    right_prefix, right_body = split_prefix(right)
    return left_prefix == right_prefix and _charset_for(left_body) == _charset_for(
        right_body
    )
