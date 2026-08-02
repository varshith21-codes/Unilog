---
name: premium-ui-design
description: Design and build web interfaces with the visual craft of Framer showcase sites, Linear, Stripe, and Fortune 100 brand sites. Use when building or restyling any landing page, marketing site, dashboard, or app UI; when the user asks for a design that looks premium, high-end, polished, expensive, modern, or "not AI-generated"; when choosing typography, color, spacing, or layout; when setting up design tokens or a Tailwind theme; or when an existing UI looks generic, flat, or cheap and needs elevating.
license: MIT
compatibility: Framework-agnostic guidance. Code examples target CSS, Tailwind CSS v4, and React/Next.js. Assumes a modern evergreen browser baseline (OKLCH, container queries, :has).
metadata:
  author: axiom-team
  version: "1.0.0"
---

# Premium UI design

Premium is not decoration. It is the visible result of a small number of decisions applied without exception. Cheap-looking UI is almost always inconsistent UI: nine font sizes, five near-identical grays, arbitrary padding, shadows at random depths.

Work in this order. Do not skip step 1.

## 1. Commit to a visual direction before writing markup

Never open with generic unopinionated code. Pick one direction and name it out loud, then make every later decision serve it. If the user has a brand, derive the direction from it; if not, propose one and say why.

| Direction | Type | Color | Shape | Motion |
|---|---|---|---|---|
| **Editorial** | Oversized serif or grotesk display, tight tracking | Near-monochrome, one ink accent | Sharp corners, hairline rules, visible grid | Restrained, opacity-led |
| **Technical** | Grotesk + monospace pairing | Dark-dominant, single luminous accent | Small radii, 1px borders, dense data | Fast, precise, staggered |
| **Organic** | Humanist sans, generous leading | Soft multi-stop gradients, warm neutrals | Large radii (16-28px), glass layers | Spring physics, gentle overshoot |
| **Cinematic** | Light display weights, huge scale contrast | Deep neutrals, image-derived accents | Full-bleed frames, minimal chrome | Slow cross-fades, scroll-linked |

Anti-pattern: blending all four. That is what generic looks like.

## 2. Build the token layer first

Every value in the UI resolves to a token. If you are typing a raw hex, px, or shadow into a component, stop and add a token instead.

Load `references/design-tokens.md` for the complete OKLCH color scale recipe, fluid type scale, spacing ramp, radii, and elevation system. Load `references/tailwind-setup.md` for the Tailwind v4 `@theme` wiring and shadcn/ui alignment.

The short version:

- **Color in OKLCH**, not hex. It is perceptually uniform, so a lightness ramp reads as evenly spaced, and you can hold hue and chroma constant while stepping lightness. Chroma peaks mid-scale and tapers at both ends.
- **One accent hue.** A second accent needs a stated reason. Semantic colors (success, warning, danger) are not accents.
- **Neutrals must be tinted.** Pure `#000`, `#fff`, and pure-gray ramps look unfinished. Carry 0.005-0.02 chroma at your brand hue through the whole neutral scale.
- **Type scale is geometric**, roughly 1.2 ratio for UI and 1.333 for editorial display. Use `clamp()` so display sizes are fluid.
- **Spacing is a 4px-based ramp**, not arbitrary. Section padding scales with viewport; component padding does not.

## 3. Typography carries most of the perceived quality

- **Two families maximum.** One is often better. A third is a bug unless it is a monospace for data.
- **Use a variable font.** Weight and optical size axes are what let display and body text share a family without looking flabby.
- **Extreme scale contrast.** Display type wants `clamp(2.5rem, 6vw, 6rem)`. Body copy stays 16-18px and never shrinks below 14px for anything a user must read.
- **Tracking scales inversely with size.** Display: `-0.02em` to `-0.04em`. Body: `0`. Small caps and overlines: `+0.06em` to `+0.12em`.
- **Line height scales inversely too.** Display `1.0-1.1`, headings `1.2`, body `1.5-1.65`.
- **Measure**: 60-75 characters for body text. Set `max-width: 65ch` on prose, not a px width.
- Set `font-variant-numeric: tabular-nums` on any number that sits in a column or updates in place. Misaligned digits in a dashboard read as amateur instantly.
- Enable `text-wrap: balance` on headings and `text-wrap: pretty` on paragraphs to kill orphans.

## 4. Space is the cheapest luxury signal

- **Vertical rhythm beats horizontal decoration.** Section padding of `clamp(5rem, 12vh, 10rem)` does more for perceived quality than any gradient.
- **Fewer things per section.** One idea, one visual, one action. Whitespace is not wasted space; it is what tells the eye something is deliberate.
- **Consistent container.** One max-width (1200-1440px typical) with a single gutter token. Full-bleed elements break out intentionally, not accidentally.
- **Spacing communicates grouping.** A label 4px from its input and 24px from the next field is legible. Uniform 12px everywhere is not.
- **Optical alignment over mathematical.** Icons, quotes, and round shapes usually need 1-2px of manual nudge to look aligned.

## 5. Depth: restraint over drop shadows

- **Borders before shadows.** A `1px` border at low-alpha foreground reads cleaner than a soft shadow. In dark themes use a light-alpha border on top and a darker one underneath to simulate a lit edge.
- **Shadows are tinted and layered.** Never `rgba(0,0,0,0.1)` alone. Stack two or three shadows with increasing blur and decreasing alpha, tinted toward the surface hue.
- **Glass needs a border.** `backdrop-filter: blur()` without a hairline semi-transparent border looks like a mistake. Always pair them, and always give glass something worth blurring behind it.
- **Grain kills sterility.** An SVG noise overlay at `opacity: 0.015-0.04` with `mix-blend-mode: overlay` is the single highest-ratio texture trick. Keep it subtle enough that it reads as paper, not static.
- **Gradients should be multi-stop and low-contrast.** Two-stop saturated gradients are a hallmark of template design. Use 3+ stops within a narrow lightness band, or a large soft radial glow behind content.

## 6. Compose sections, do not stack cards

Load `references/section-patterns.md` for concrete structures: hero architectures, bento grids, feature rows, pricing, social proof, footers, and navigation behaviour.

Rules that apply to all of them:

- **The hero must state what this is in one sentence a stranger understands.** Craft cannot rescue a vague headline.
- **Vary section shape.** Alternating a centered section, an asymmetric split, a bento grid, and a full-bleed band creates rhythm. Six identical three-column card rows is the most common generic tell.
- **Every section needs a reason to exist.** If it does not advance understanding or trust, delete it.
- **Real content, real density.** Design against realistic strings and realistic data volumes, including the longest plausible label and the empty state.

## 7. Interaction states are not optional

Every interactive element defines: rest, hover, active/pressed, focus-visible, disabled, and loading. Missing states are the most common gap between a demo and a product.

- **Focus rings must be visible and designed.** `:focus-visible` with a 2px offset ring in the accent color. Never `outline: none` without a replacement.
- **Hover should change more than one property**, subtly: background plus border plus a 1-2px transform.
- **Hit targets are 44px minimum** on touch, 32px minimum on pointer.
- **Wrap pointer-dependent flourishes** in `@media (hover: hover) and (pointer: fine)`.

## 8. Dark mode is a separate design, not an inversion

- Elevated surfaces get **lighter** in dark mode, not darker.
- Reduce chroma of accents slightly in dark mode; saturated colors vibrate on dark backgrounds.
- Pure white text on pure black is harsh. Use `oklch(0.97 0.005 <hue>)` on `oklch(0.16 0.01 <hue>)`.
- Shadows barely register on dark surfaces. Communicate elevation with border lightness and surface lightness instead.

## 9. Accessibility is part of the craft

- Body text at 4.5:1 minimum, large text at 3:1, UI borders and icons at 3:1. Verify, do not assume.
- Never encode meaning in color alone. Pair status color with an icon or label.
- Respect `prefers-reduced-motion` and `prefers-contrast`.
- Semantic HTML first. A `div` with a click handler is a defect.
- Full WCAG conformance needs manual testing with assistive technology and expert review; these rules are the floor, not a certificate.

## 10. Performance is a design constraint

- Animate only `transform` and `opacity`. Animating `width`, `height`, `top`, or `margin` causes layout thrash.
- Subset and preload fonts; set `font-display: swap`; define `size-adjust` fallbacks to avoid layout shift.
- Reserve dimensions for every image and embed. CLS is a visible quality defect.
- Ship the hero without waiting on JavaScript. A blank first paint undoes all of this.

## Non-negotiables

1. No raw values in components. Tokens only.
2. Two font families maximum.
3. One accent hue unless justified.
4. Every interactive element has all six states.
5. Only `transform` and `opacity` animate.
6. `prefers-reduced-motion` respected.
7. Contrast verified, not assumed.
8. Designed empty, loading, and error states.
9. No section without a purpose.
10. Tested at 360px, 768px, 1280px, and 1920px.

## Companion skills

- `premium-motion` for easing tokens, scroll-linked reveals, and interaction physics.
- `ui-design-review` to audit an existing UI against this bar before shipping.
