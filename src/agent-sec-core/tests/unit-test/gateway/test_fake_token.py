"""Unit tests for placeholder credential generation.

The point of these tests is the shape contract: a placeholder that does not look
like the real credential can be rejected by the client before the request ever
reaches the gateway, which would look like a broken credential rather than a
broken gateway.
"""

import pytest
from agent_sec_cli.gateway.fake_token import (
    GITHUB_PAT_TOTAL_LENGTH,
    generate_fake_token_like,
    generate_github_fake_token,
    is_github_pat_shaped,
    same_shape,
    split_prefix,
)

# Representative shapes per provider. The values are invented; only their
# structure matters here.
_SHAPES = [
    ("ghp_16CharsAndMore0123456789abcdefGHIJ", "ghp_", "github classic pat"),
    ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345", "sk-ant-api03-", "anthropic"),
    ("sk-proj-abcdefghijklmnopqrstuvwxyz0123456789", "sk-proj-", "openai project"),
    ("xoxb-123456789012-1234567890123-abcdefgh", "xoxb-", "slack bot"),
    ("deadbeefcafebabe0123456789abcdef", "", "lowercase hex, no prefix"),
    ("DEADBEEFCAFEBABE0123456789ABCDEF", "", "uppercase hex, no prefix"),
]


@pytest.mark.parametrize(("real", "prefix", "label"), _SHAPES)
def test_prefix_detection(real: str, prefix: str, label: str) -> None:
    assert split_prefix(real)[0] == prefix


@pytest.mark.parametrize(("real", "prefix", "label"), _SHAPES)
def test_generated_placeholder_keeps_shape(real: str, prefix: str, label: str) -> None:
    fake = generate_fake_token_like(real)

    assert len(fake) == len(real)
    assert fake.startswith(prefix)
    assert same_shape(real, fake)
    assert fake != real


def test_hex_credential_gets_a_hex_safe_marker() -> None:
    """AGENTSEC is not valid hex, so the marker degrades instead of corrupting."""
    fake = generate_fake_token_like("deadbeefcafebabe0123456789abcdef")

    assert all(char in "0123456789abcdef" for char in fake)


def test_short_credential_drops_the_marker_rather_than_overflow() -> None:
    real = "shortkey123"
    fake = generate_fake_token_like(real)

    assert len(fake) == len(real)


def test_separatorless_prefix_needs_keep_prefix() -> None:
    """Google-style keys (AIza...) carry no separator, so autodetect cannot help."""
    real = "AIzaSyA0123456789abcdefghijklmnopqrstu"

    assert not generate_fake_token_like(real).startswith("AIza")

    kept = generate_fake_token_like(real, keep_prefix=4)
    assert kept.startswith("AIza")
    assert len(kept) == len(real)


def test_keep_prefix_out_of_range_is_rejected() -> None:
    real = "AIzaSyA0123456789"

    with pytest.raises(ValueError):
        generate_fake_token_like(real, keep_prefix=len(real))


def test_empty_real_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        generate_fake_token_like("   ")


def test_github_fallback_shape() -> None:
    token = generate_github_fake_token()

    assert len(token) == GITHUB_PAT_TOTAL_LENGTH
    assert is_github_pat_shaped(token)
    assert token.endswith("AGENTSEC")


def test_github_fallback_rejects_non_base62_marker() -> None:
    with pytest.raises(ValueError):
        generate_github_fake_token(marker="not-base62")


def test_same_shape_detects_length_mismatch() -> None:
    assert not same_shape("ghp_aaaa", "ghp_aaaaa")


def test_generated_placeholders_are_unique() -> None:
    real = "ghp_16CharsAndMore0123456789abcdefGHIJ"

    assert generate_fake_token_like(real) != generate_fake_token_like(real)
