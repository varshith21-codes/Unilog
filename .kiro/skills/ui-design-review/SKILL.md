---
name: ui-design-review
description: Audit an existing web UI against a premium visual bar and return a prioritized list of concrete fixes. Use when a UI looks generic, flat, cheap, unfinished, or "AI-generated" and the user cannot say why; when reviewing a page, component, or screenshot before shipping; when asked to critique, polish, elevate, or tighten an interface; or as a final quality gate after implementing frontend work. Diagnoses inconsistent tokens, weak typography, arbitrary spacing, template-grade layout, missing interaction states, motion defects, contrast failures, and performance problems.
license: MIT
compatibility: Reviews source code, live pages, or screenshots. Contrast and performance findings require running the page; static review flags them as unverified.
metadata:
  author: axiom-team
  version: "1.0.0"
---

# UI design review

Diagnostic pass over an existing interface. The goal is not a list of opinions but a ranked set of changes where each one names the file, the current value, the target value, and the reason.

Target for review: $ARGUMENTS

## How to run the review

1. **Establish what it is trying to be.** A review against the wrong intent is noise. Identify the product type (marketing site, dashboard, review tool) and the apparent visual direction. If no direction is discernible, that is finding number one.
2. **Read the token layer first.** Open the global stylesheet or theme config. Most quality problems are visible here before you look at a single component.
3. **Inventory the drift.** Count distinct font sizes, colors, radii, shadows, and spacing values actually used. Compare against what the token layer defines. The gap between the two is the core of most reviews.
4. **Walk the seven axes below**, in order.
5. **Rank findings** by visual impact per unit of effort, not by severity alone.
6. **Report** using the output format at the end.

Load `references/generic-ui-tells.md` for the catalogue of specific patterns that make a UI read as template-grade or machine-generated, with the fix for each.

## Axis 1: Token discipline

The highest-signal axis. Run an actual count.

- How many distinct `font-size` values appear? More than 8 in a product, more than 6 in a marketing page, indicates drift.
- How many distinct colors resolve in the rendered output? Near-identical grays (`#f8f9fa` and `#f9fafb` in the same file) are the classic symptom.
- How many `border-radius` values? More than 4 without a stated reason means it was decided per component.
- How many shadow definitions? Single-layer `rgba(0,0,0,0.1)` shadows are a flatness tell.
- Are there raw hex, px, or rem values inside components rather than token references?
- Are neutrals pure gray or tinted? Pure gray reads unfinished.

Report as: `47 distinct spacing values across 12 components; token scale defines 14. Consolidate to the scale.`

## Axis 2: Typography

- Font families in use. More than two (excluding a monospace for data) needs justification.
- Scale contrast: is there real jump between display and body, or does everything sit between 14px and 24px? Flat scale is the most common cause of "it looks bland."
- Is tracking adjusted at display sizes? Large text at default tracking looks loose and amateurish.
- Line height: body between 1.5 and 1.65; headings 1.1-1.25. Uniform 1.5 everywhere is a tell.
- Measure: is prose constrained to roughly 65-75 characters, or does it run the full container width?
- `tabular-nums` on numeric columns and live-updating figures?
- `text-wrap: balance` on headings? Any visible orphans or widows?
- Are font weights real variable-font axis values, or is the browser synthesizing bold?

## Axis 3: Space and layout

- Is spacing on a consistent scale, or arbitrary (13px, 17px, 22px)?
- Does spacing communicate grouping, or is it uniform everywhere? Uniform spacing removes hierarchy.
- Section padding: does it scale with viewport? Fixed 48px sections look cramped on desktop.
- Container: one max-width consistently applied, or several?
- Section shape variety: count consecutive sections with the same structure. Three or more identical card grids is a finding.
- Alignment: do elements share edges across sections, or does each section have its own inset?
- Any horizontal overflow at 360px?
- Density appropriate to purpose? A data table with 64px rows wastes the screen; marketing copy at 40px line height reads as a spreadsheet.

## Axis 4: Color and depth

- One accent hue, or several competing?
- Is the accent used for emphasis, or sprayed across every element until nothing is emphasized?
- Are semantic colors distinguishable from brand accent?
- Dark mode: do elevated surfaces get lighter? Is accent chroma reduced? Or is it a naive inversion?
- Shadows: layered and tinted, or single flat black?
- Glass surfaces: paired with a hairline border, or blur alone?
- Gradients: multi-stop within a narrow band, or a saturated two-stop diagonal?
- Any texture at all, or is every surface a flat fill?

## Axis 5: Interaction states

Enumerate every interactive element and check all six states. Missing states are the widest gap between demo-grade and product-grade.

| State | Common failure |
|---|---|
| Rest | Insufficient affordance; a button that looks like text |
| Hover | Single-property change, or missing entirely |
| Active/pressed | Almost always missing; costs a `scale(0.98)` |
| Focus-visible | `outline: none` with no replacement — an accessibility defect |
| Disabled | Reduced opacity only, still appears clickable, no cursor change |
| Loading | Missing, or a layout-shifting spinner swap |

Also check: hit targets at 44px on touch, cursor styles correct, pointer-only effects guarded by `@media (hover: hover) and (pointer: fine)`, and destructive actions visually distinct from safe ones.

## Axis 6: Motion

- Any motion at all? A completely static UI reads as unfinished in 2026.
- Too much? Everything animating means nothing is emphasized.
- Durations: hover over 200ms feels laggy; modals under 250ms feel violent.
- Easing: any `linear` or default `ease` on UI transitions?
- Are exits faster than entrances?
- Only `transform` and `opacity` animated? Flag any animated `width`, `height`, `top`, `margin`, `box-shadow`, or `filter`.
- Do scroll reveals fire once, or re-trigger on every pass?
- Is `prefers-reduced-motion` handled in every animated component?
- Stagger present on lists, or do they appear as one block?
- Does layout animate on reorder and filtering, or do items jump?

## Axis 7: Content, states, and accessibility

- Empty, loading, and error states designed, or afterthoughts?
- Does the design survive realistic content: the longest label, a 40-character product name, zero rows, 10,000 rows?
- Is copy specific, or filled with claims that could describe any product?
- Placeholder content still present: lorem ipsum, stock illustrations, `#` links, fake logos?
- Semantic HTML, or `div` soup with click handlers?
- Heading hierarchy sequential, one `h1` per page?
- Contrast verified against actual rendered pairs? `--fg-secondary` on a tinted surface is the pair that most often fails.
- Images have dimensions reserved and meaningful `alt`?
- Keyboard: can every action be completed without a mouse? Any focus traps outside modals?
- Full WCAG conformance requires manual assistive-technology testing and expert review. Report what was checked and what was not.

## Prioritization

Rank by impact per effort, then report in that order:

1. **Broken** — accessibility defects, contrast failures, unusable states, horizontal overflow. Fix regardless of cost.
2. **High leverage** — token consolidation, type scale contrast, section rhythm. Small diffs, large visible change.
3. **Craft** — depth treatment, micro-interactions, texture, stagger. What separates good from premium.
4. **Optional** — hero moments and flourishes. Only after 1-3 are clean.

A UI at level 1 and 2 already looks competent. Level 3 is where it starts reading as expensive. Do not propose level 4 work while level 1 findings are open.

## Output format

```markdown
## Verdict
One paragraph: what this UI currently reads as, and the single change with the largest effect.

## Findings

### 1. [Broken] Focus rings removed globally
- Where: `app/globals.css:34`
- Current: `*:focus { outline: none; }`
- Target: remove the reset; add `:focus-visible` with a 2px accent ring at 2px offset
- Why: keyboard users cannot see where they are. Blocks accessibility conformance.

### 2. [High leverage] Type scale has no contrast
- Where: across `components/`
- Current: 9 sizes between 14px and 28px; hero headline at 28px
- Target: consolidate to 6 UI steps; hero to `clamp(2.75rem, 1.6rem + 5vw, 4.629rem)`
- Why: without scale contrast nothing reads as primary; this is the main reason the page feels flat.

[continue, ranked]

## Not verified
- Contrast ratios (needs the page running)
- Frame timings (needs a performance profile)
```

Be specific and quantitative. "Improve the spacing" is not a finding. "Section padding is a fixed 48px; move to `clamp(4rem, 10vh, 9rem)` so desktop gets breathing room" is.

State what you inspected and what you could not. If you reviewed source without running the page, say so rather than implying contrast and performance were measured.

## Companion skills

- `premium-ui-design` for the visual system the findings should move toward.
- `premium-motion` for the motion tokens and recipes referenced in Axis 6.
