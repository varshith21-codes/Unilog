# Catalogue of generic UI tells

Specific patterns that make an interface read as template-grade or machine-generated, each with the fix. When a user says a UI looks "AI-generated" or "cheap" without being able to explain why, the cause is almost always on this list.

## Layout tells

**Everything is a centered card grid.**
Three columns of equal cards, repeated for every section. Fix: vary section shape. Alternate asymmetric splits, a bento grid with one anchor cell, a full-bleed band, and a sticky-scroll section. Never three consecutive identical grids.

**Symmetry everywhere.**
Every section centered, every element balanced. Reads as a template because templates cannot make editorial decisions. Fix: give at least two sections an asymmetric 5/7 or 4/8 split; let one visual break the container edge.

**Uniform section padding.**
Fixed `py-16` on every section regardless of importance. Fix: fluid `clamp(4rem, 10vh, 9rem)` as the default, with a larger token for the hero and closing CTA.

**Content that never touches an edge.**
Everything inset inside the container with no full-bleed elements. Fix: one or two intentional bleeds. A visual that runs off the right edge signals confidence.

**Equal visual weight for everything.**
No focal point. Fix: pick the one element per section that matters and make it clearly dominant in scale or contrast.

## Typography tells

**Compressed type scale.**
Everything between 14px and 24px. Nothing reads as a headline. Single most common cause of "it looks bland." Fix: hero at `clamp(2.75rem, 1.6rem + 5vw, 4.6rem)`; keep body at 16-17px. The gap is the point.

**Default tracking at display sizes.**
48px text at `letter-spacing: normal` looks loose. Fix: `-0.02em` to `-0.04em` on display, `0` on body, positive tracking only on small uppercase overlines.

**Uniform line height.**
`1.5` on everything including the hero headline. Fix: `1.08` display, `1.22` headings, `1.6` body.

**Three or more font families.**
Fix: two maximum, or one variable font plus a monospace for data.

**Prose at full container width.**
120-character lines. Fix: `max-width: 65ch` on paragraphs.

**Synthesized bold.**
Browser faking weight because the font file lacks the axis. Looks smeared. Fix: load a variable font or the real weight files.

**Misaligned digits.**
Numbers in a table jittering because they are proportional. Fix: `font-variant-numeric: tabular-nums`.

**Orphans in headings.**
One word alone on the last line. Fix: `text-wrap: balance`.

## Color tells

**Pure black and pure white.**
`#000` and `#fff` with a pure-gray ramp. Reads as unfinished because nothing in the physical world is neutral. Fix: carry 0.005-0.02 chroma at the brand hue through the whole neutral scale.

**Near-duplicate grays.**
`#f8f9fa`, `#f9fafb`, and `#fafafa` in the same stylesheet. Symptom of per-component decisions. Fix: consolidate to one scale, delete the rest.

**Two-stop saturated diagonal gradient.**
Purple to pink at 135 degrees. The single most recognizable template signature. Fix: 3+ stops inside a narrow lightness band, or a large soft radial glow behind content instead of a fill.

**Accent color everywhere.**
Every icon, border, and heading in the brand color, so nothing stands out. Fix: accent on primary actions and one focal element per view. Neutrals do the rest.

**Multiple competing accents.**
Fix: one accent hue. Semantic colors are not accents.

**Naive dark mode.**
Colors inverted, elevated surfaces going darker, accents at full chroma vibrating against the background. Fix: elevated surfaces get lighter; reduce accent chroma; near-white text on near-black rather than pure values.

**Status by color alone.**
Fix: pair the color with an icon or a label.

## Depth tells

**Single flat shadow.**
`box-shadow: 0 4px 6px rgba(0,0,0,0.1)` on everything. Fix: stack two or three shadows with increasing blur and decreasing alpha, tinted toward the surface hue.

**Shadows in dark mode.**
Barely visible, so the UI looks flat. Fix: communicate elevation through surface and border lightness instead.

**Glass without a border.**
`backdrop-filter: blur()` alone looks like a rendering error. Fix: always pair with a hairline semi-transparent border, and ensure there is something worth blurring behind it.

**Glass over a flat fill.**
Blur with nothing behind it. Pure cost, no effect. Fix: remove it or put content behind it.

**Every surface a flat fill.**
No grain, no gradient, no border variation. Digitally sterile. Fix: SVG noise overlay at `opacity: 0.015-0.04` with `mix-blend-mode: overlay`.

**Mixed radii without intent.**
8px cards, 4px buttons, pill badges, 16px modals. Fix: pick one radius family. Inner radius equals outer radius minus the gap.

## Content tells

**Headline that could belong to anyone.**
"Transform your workflow." "Built for modern teams." Fix: one sentence a stranger understands that names what the product actually does. No amount of visual craft rescues a vague headline.

**Stock illustrations and 3D blobs.**
Abstract shapes with no relationship to the product. Fix: show the real product surface. A readable screenshot beats any illustration.

**Screenshot too small to read.**
A dashboard shrunk until the text is illegible. Fix: crop to the one region that makes the point, at readable scale.

**Three equal-weight buttons.**
Fix: one primary, one secondary as a text link.

**Lorem ipsum and `#` links.**
Fix: real copy and real destinations before review.

**Fake logo strips.**
Invented company logos, or real ones without permission. Fix: use actual customers, or remove the section.

**Three short testimonials in a card row.**
Fix: one substantial quote with a real name, role, company, and photo.

**Round metrics.**
"99%+ accuracy" reads as marketing. Fix: `99.2%` reads as measured.

**Icon grid where every icon is decorative.**
Six features, six generic icons, no information. Fix: fewer features, each with a real visual, or drop the icons and let the type work.

## State tells

**No hover states.**
Or hover changing only `background-color`. Fix: change background, border, and a 1-2px transform together.

**No pressed state.**
Fix: `scale(0.98)` plus `translateY(1px)` at 100ms.

**`outline: none` with no replacement.**
An accessibility defect, not a style choice. Fix: `:focus-visible` with a designed 2px ring at 2px offset.

**Disabled by opacity only.**
Still looks clickable. Fix: reduce opacity, remove pointer events, change the cursor, and remove the shadow.

**No empty state.**
A blank region where content would be. Fix: one line explaining what goes here plus the action that creates it.

**No loading state, or a layout-shifting spinner.**
Fix: skeletons matching the real content's dimensions.

**No error state.**
Fix: what failed, why, and a retry action.

**Design that breaks on real content.**
Truncated labels, overflowing names, a table that collapses at 10,000 rows. Fix: design against the longest plausible string and the largest plausible dataset.

## Motion tells

**Nothing moves.**
Reads as unfinished. Fix: at minimum, hover transitions, a staggered entrance on the primary list, and one scroll reveal.

**Everything moves.**
Every element animating in. Fix: one hero moment per page.

**`linear` easing on UI.**
Mechanical. Fix: ease-out for entrances, ease-in for exits.

**Default `ease`.**
A weak curve that reads as generic because it is the browser default. Fix: defined bezier tokens.

**Uniform durations.**
`transition: all 300ms` everywhere. `all` also animates properties you did not intend. Fix: duration tokens per interaction class; enumerate the properties.

**Slow hover.**
300ms+ on hover feels broken. Fix: 120-180ms.

**Bouncy overshoot on large surfaces.**
A modal springing past its position looks like a bug. Fix: cap `bounce` at 0.2 for anything larger than a card.

**Re-triggering scroll reveals.**
Elements re-animating on every scroll pass. Fix: `once: true`.

**Animating layout properties.**
`width`, `height`, `top`, `margin` in a transition. Causes jank. Fix: `transform` and `opacity` only.

**Reduced motion ignored.**
Fix: `prefers-reduced-motion` in every animated component; keep the fade, drop the travel.

**Aggressive parallax.**
Fix: keep displacement within ±10%.

**Custom cursor on touch.**
Dead zones and broken taps. Fix: `@media (hover: hover) and (pointer: fine)`.

## Structural tells

**Raw values in components.**
`padding: 13px`, `color: #6b7280` inline. Guarantees drift. Fix: tokens only.

**`div` with an onClick.**
Not focusable, not announced, no keyboard activation. Fix: a real `button`.

**Skipped heading levels.**
`h1` to `h4` because of how it looked. Fix: correct hierarchy, style separately.

**Multiple `h1` elements.**
Fix: one per page.

**Images without reserved dimensions.**
Layout shift on load, which is a visible quality defect. Fix: explicit `width` and `height` or an aspect-ratio box.

**Fonts from a third-party CDN at runtime.**
Flash of unstyled text plus a privacy concern. Fix: self-host and preload with a size-adjusted fallback.

**Hero gated on JavaScript.**
Blank first paint undoes every other decision. Fix: server-render the hero.

## Quick triage

If time is short, check these six. They account for most of the gap between generic and premium:

1. Count distinct font sizes and colors. Consolidate.
2. Is there real display-to-body scale contrast?
3. Do consecutive sections vary in shape?
4. Do all interactive elements have all six states, including `:focus-visible`?
5. Are shadows layered and neutrals tinted?
6. Is there any stagger or scroll reveal, firing once, respecting reduced motion?
