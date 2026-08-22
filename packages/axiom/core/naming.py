"""One SKU, one safe identifier — for a filename, and for a URL path segment.

Industrial part numbers are not identifiers. The client's own item master carries
``52C3-5/8-UPC``, ``SHOP/4X2/840/V1`` and ``MAG:2044-230-1``, and a fractional size written with a
solidus is completely ordinary in this domain rather than an edge case. Every per-SKU artifact in
this repository is stored at ``{sku}.json``, so those part numbers were silently unstorable: the
write either landed in a directory that does not exist or created one nobody meant to create. The
session endpoint noticed the same problem from the other side and rejected any SKU containing a
separator outright, which is correct as a traversal guard and useless as an answer.

:func:`sku_slug` is the answer. It maps a SKU onto a single path segment that is safe as a filename
on Windows and POSIX, safe unencoded inside a URL, and **injective** — distinct SKUs cannot collide
on one file, which matters because a collision here would silently merge two products' review
history.

The scheme is percent-encoding with ``~`` as the escape character rather than ``%``:

    52C3-5/8-UPC     ->  52C3-5~2F8-UPC
    MAG:2044-230-1   ->  MAG~3A2044-230-1
    BA-100-075       ->  BA-100-075          (unchanged, and that is load-bearing)

``~`` rather than ``%`` because a literal ``%`` in a URL path is itself an escape introducer: a
browser or proxy re-decoding ``%2F`` mid-path turns one segment back into two, which is the exact
bug being fixed. ``~`` is unreserved in RFC 3986, needs no encoding, and no filesystem treats it
specially.

**A SKU that needs no escaping is returned unchanged**, so every artifact already on disk
(``BA-100-075.bundle.json``) keeps resolving and no migration is required.
"""

from __future__ import annotations

# Unreserved in RFC 3986 minus `~`, which is reserved here as the escape introducer. Every one of
# these is also legal in a filename on both Windows and POSIX.
_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
)

# Reserved device names on Windows. `CON.json` is not creatable, whatever the extension, so a SKU
# that happens to be one is escaped rather than left to fail at write time on one platform only.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


def sku_slug(sku: str) -> str:
    """A filename- and URL-safe single path segment for ``sku``.

    Raises ``ValueError`` on an empty SKU: there is no safe name for a product with no identifier,
    and inventing one would put an unattributable artifact on disk.
    """
    if not sku or not sku.strip():
        raise ValueError("cannot derive a slug from an empty SKU")

    out: list[str] = []
    for char in sku:
        if char in _SAFE:
            out.append(char)
        else:
            # Escape the UTF-8 bytes, not the code point, so the result stays ASCII on any
            # filesystem whose encoding is not UTF-8.
            out.extend(f"~{byte:02X}" for byte in char.encode("utf-8"))
    slug = "".join(out)

    # A leading dot would make the artifact a hidden file, and `.`/`..` would name a directory
    # rather than a file — the case the API's traversal guard exists to catch.
    if slug.startswith("."):
        slug = "~2E" + slug[1:]

    # Escaping the first character rather than prefixing keeps the mapping injective: every `~` in
    # a slug is followed by exactly two hex digits, so no escaped form can collide with a literal.
    if slug.upper() in _WINDOWS_RESERVED:
        slug = f"~{ord(slug[0]):02X}" + slug[1:]

    return slug


def is_sku_slug(value: str) -> bool:
    """Whether ``value`` is already a well-formed slug, and therefore safe to use as a path.

    A whitelist, not a blacklist, and that is the point. The alternative — reject the characters
    known to be dangerous — has to enumerate ``/``, ``\\``, ``..``, NTFS alternate data streams
    (``name:stream``), device names and whatever the next platform adds. This asks the only
    question that is actually decidable: is every character one of the sixty-five this scheme
    emits?

    Callers receiving a SKU from a URL should validate with this rather than slug the value again.
    :func:`sku_slug` is deliberately *not* idempotent — it escapes ``~``, because a scheme that
    left its own escape character alone could not distinguish a literal ``~`` from an escape and
    would not be injective.
    """
    if not value or value in {".", ".."}:
        return False
    if value.startswith("."):
        return False
    if value.upper() in _WINDOWS_RESERVED:
        return False
    return all(char in _SAFE or char == "~" for char in value)


__all__ = ["is_sku_slug", "sku_slug"]
