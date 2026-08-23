"""A document's title block: the few lines that name the product, and little else.

Small, and it decides whether a retrieved datasheet is usable at all when the item-master row has no
description.

Classification by retrieval abstains unless one candidate *dominates* the next, which is the right
rule and the reason precision is what it is. But it makes the amount of text you hand it
load-bearing. A whole datasheet mentions the vocabulary of several neighbouring classes — measured
on ``data/samples/ba100.txt``, a two-piece bronze ball valve's spec sheet
scores:

    0.5346  Plumbing > Valves > Ball Valves > Two-Piece
    0.4680  Plumbing > Valves > Gate Valves > Bronze

That margin is genuinely thin, and abstaining on it (``ambiguous_no_model``) is correct: the body
text discusses bronze, NPT threads, WSP steam ratings and temperature derating, all of which a gate
valve also has. The same document's opening lines score 0.7596 and decide it, because a title block
names the product and does nothing else:

    MILWAUKEE VALVE - BA-100 SERIES
    Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08

So this is not a trick to make the classifier agree — it is handing it the *identifying* text rather
than the whole document, which is the same kind of input the description path gives it. A
model-backed classifier can take the full text and resolve the ambiguity itself; a deterministic one
has to be given text that is not ambiguous.

Same reasoning as :mod:`axiom.docintel.revision`, which confines its scan to a header window for the
mirror-image reason: a revision marker deep in body prose is probably describing a *different*
document.
"""

from __future__ import annotations

DEFAULT_LINES = 6
"""How many non-empty lines count as the title block.

Six because a datasheet heading is a product name and a subtitle, sometimes with a revision marker
or a part-series line between them. Enough to name the product; short enough that the body's
vocabulary cannot blur it.
"""


def title_block(parsed, *, max_lines: int = DEFAULT_LINES) -> str:
    """The opening non-empty lines of a parsed document.

    Reads the *parsed* lines rather than raw text so it behaves identically for a PDF, an HTML page
    and a text file — a PDF's title is a set of positioned lines, not the first bytes of the file.
    """
    lines: list[str] = []
    for line in parsed.all_lines():
        text = line.text.strip()
        if not text:
            continue
        lines.append(text)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


__all__ = ["DEFAULT_LINES", "title_block"]
