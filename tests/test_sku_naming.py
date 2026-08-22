"""The SKU slug contract, pinned on both sides of the seam.

Every case in ``CASES`` is also asserted in ``apps/console/src/lib/sku.test.ts``. The two
implementations exist because one names files in Python and the other builds URLs in TypeScript,
and keeping one table in front of both is what stops them drifting into a link that 404s or a
review decision written to the wrong file.

The behaviour under test is not cosmetic. Before it existed, a part number containing a solidus —
``52C3-5/8-UPC``, a fractional size and completely ordinary in industrial distribution — could not
be stored at ``data/console/{sku}.bundle.json`` at all: the write landed in a directory that did
not exist. Eight of the thousand rows in the client's own sample are in that shape.
"""

from __future__ import annotations

import pytest
from axiom.core.naming import is_sku_slug, sku_slug

# (sku, slug) — mirrored verbatim in apps/console/src/lib/sku.test.ts.
CASES: list[tuple[str, str]] = [
    # Unchanged, and that is load-bearing: every artifact already on disk must keep resolving.
    ("BA-100-075", "BA-100-075"),
    ("T-113-100", "T-113-100"),
    ("3MABR-7100075678", "3MABR-7100075678"),
    ("a.b_c-1", "a.b_c-1"),
    # Real part numbers from the client's item master.
    ("52C3-5/8-UPC", "52C3-5~2F8-UPC"),
    ("72171-3/4-1W-UPC", "72171-3~2F4-1W-UPC"),
    ("SHOP/4X2/840/V1", "SHOP~2F4X2~2F840~2FV1"),
    ("MAG:2044-230-1", "MAG~3A2044-230-1"),
    ("2/2/4 UD ALUM", "2~2F2~2F4~20UD~20ALUM"),
    ("FS C01 2004S", "FS~20C01~202004S"),
    # The escape character itself, so the mapping stays injective.
    ("A~B", "A~7EB"),
    # A leading dot would make a hidden file; `.` and `..` would name a directory.
    (".hidden", "~2Ehidden"),
    ("..", "~2E."),
    # Windows device names are not creatable whatever the extension.
    ("CON", "~43ON"),
    ("com1", "~63om1"),
    # Non-ASCII escapes as UTF-8 bytes, not as a code point.
    ("\u00c5-1", "~C3~85-1"),
]


@pytest.mark.parametrize(("sku", "slug"), CASES)
def test_slug_matches_the_pinned_table(sku: str, slug: str) -> None:
    assert sku_slug(sku) == slug


def test_slug_is_injective_across_the_table() -> None:
    """The property that matters more than any single mapping.

    Two products colliding on one filename would silently merge their review history, and nothing
    downstream could detect it — the second write simply wins.
    """
    slugs = [sku_slug(sku) for sku, _ in CASES]
    assert len(set(slugs)) == len(slugs)


@pytest.mark.parametrize(("sku", "_slug"), CASES)
def test_slug_is_a_single_safe_path_segment(sku: str, _slug: str) -> None:
    slug = sku_slug(sku)
    assert "/" not in slug
    assert "\\" not in slug
    assert ":" not in slug
    assert slug not in {".", ".."}
    assert not slug.startswith(".")
    assert all(char.isalnum() or char in "-_.~" for char in slug)


def test_slug_refuses_an_empty_sku() -> None:
    """There is no safe name for a product with no identifier, and inventing one would put an
    unattributable artifact on disk."""
    for empty in ("", "   ", "\t"):
        with pytest.raises(ValueError, match="empty SKU"):
            sku_slug(empty)


def test_slug_is_not_idempotent_deliberately() -> None:
    """Re-slugging escapes the escape character.

    A scheme that left ``~`` alone could not tell a literal from an escape and would not be
    injective. Callers holding a slug must validate it rather than re-derive it, which is why the
    API uses :func:`is_sku_slug`. Asserted so nobody "fixes" this.
    """
    once = sku_slug("52C3-5/8-UPC")
    assert sku_slug(once) == "52C3-5~7E2F8-UPC"
    assert is_sku_slug(once)


@pytest.mark.parametrize(("sku", "slug"), CASES)
def test_every_slug_validates_as_one(sku: str, slug: str) -> None:
    """The round trip the API depends on: what the writer produced, the reader accepts."""
    assert is_sku_slug(sku_slug(sku))


@pytest.mark.parametrize(
    "hostile",
    [
        "",
        ".",
        "..",
        "../../etc/passwd",
        "..\\..\\windows",
        "a/b",
        "a\\b",
        "stream:name",
        ".hidden",
        "CON",
        "nul",
        "sku with spaces",
        "pct%2Fescape",
    ],
)
def test_validator_rejects_anything_the_writer_would_not_emit(hostile: str) -> None:
    """A whitelist, so the answer does not depend on enumerating every dangerous character.

    ``pct%2Fescape`` is in here on purpose: percent-encoding is what a caller reaches for by
    reflex, and accepting it would reintroduce the double-decode this scheme exists to avoid.
    """
    assert not is_sku_slug(hostile)
