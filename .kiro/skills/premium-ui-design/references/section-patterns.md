# Section and layout patterns

Concrete structures for pages that need to look considered. Vary the shape between consecutive sections; repetition of one card grid is the most recognizable generic tell.

## Page rhythm

A landing page that reads as premium alternates section *shape*, not just content:

```
1. Nav            sticky, reactive to scroll direction
2. Hero           full-bleed, asymmetric or centered, one action
3. Proof strip    logos or a single metric row, low visual weight
4. Feature A      asymmetric split, text left / visual right
5. Feature B      bento grid, mixed cell sizes
6. Feature C      full-bleed band, inverted colors
7. Deep dive      sticky-scroll, pinned visual with stepping copy
8. Social proof   one large testimonial, not a grid of three
9. Pricing        two or three tiers, one visually elevated
10. CTA           short, centered, high contrast
11. Footer        dense, organized, quietly typeset
```

Rules: never two adjacent sections with the same background treatment; never more than two centered sections in a row; every section earns its scroll cost.

## Hero architectures

### Asymmetric split (default for products)

Copy occupies 5 of 12 columns, visual breaks the container on the right. Reads as designed rather than templated.

```html
<section class="relative overflow-hidden glow-ambient">
  <div class="mx-auto grid max-w-[var(--container-wide)] items-center
              gap-[var(--space-12)] px-[var(--gutter)]
              py-[var(--section-y-lg)] lg:grid-cols-12">
    <div class="lg:col-span-5">
      <p class="text-[length:var(--text-xs)] font-medium uppercase
                tracking-[var(--tracking-overline)] text-[var(--fg-tertiary)]">
        Product intelligence
      </p>
      <h1 class="mt-[var(--space-5)] text-[length:var(--text-4xl)]
                 font-[680] leading-[var(--leading-display)]
                 tracking-[var(--tracking-display)] text-balance">
        Every attribute, traced to its source
      </h1>
      <p class="mt-[var(--space-6)] max-w-[46ch]
                text-[length:var(--text-lg)] leading-[var(--leading-body)]
                text-[var(--fg-secondary)]">
        One clear sentence a stranger understands. No adjectives that
        could describe any other product.
      </p>
      <div class="mt-[var(--space-8)] flex flex-wrap gap-[var(--space-3)]">
        <a href="#" class="btn-primary">Start free</a>
        <a href="#" class="btn-ghost">See how it works</a>
      </div>
    </div>

    <!-- Visual breaks the grid, bleeds right -->
    <div class="lg:col-span-7 lg:-mr-[12vw]">
      <div class="surface-gradient-border overflow-hidden shadow-[var(--shadow-xl)]">
        <!-- Real product surface. Never a stock illustration. -->
      </div>
    </div>
  </div>
</section>
```

### Centered editorial

For brand and category-defining statements. Requires a genuinely strong headline; there is nothing else to look at.

- Headline at `--text-5xl`, max 2 lines, `text-wrap: balance`
- Subhead capped at 55ch, `--fg-secondary`
- Single primary action, secondary as a text link
- Vertical padding at `--section-y-lg` minimum
- One soft radial glow behind the text, nothing more

### Full-bleed cinematic

Media fills the viewport; copy overlays with a scrim.

- `min-height: 100dvh` (not `100vh`, which breaks on mobile browser chrome)
- Scrim must be a gradient, not flat black: `linear-gradient(to top, oklch(0.15 0.01 264 / 0.75), transparent 60%)`
- Text sits in the lower third or bottom-left, not dead center
- Poster frame required for video; `preload="metadata"`, muted, `playsinline`
- Verify text contrast against the *brightest* region of the media

## Hero anti-patterns

- Generic 3D blob or gradient mesh with no relationship to the product
- Headline that could belong to any company ("Transform your workflow")
- Three equal-weight buttons
- A dashboard screenshot too small to read anything in
- Centered text over a busy image without a scrim

## Bento grid

Mixed cell sizes across a 12-column, 2-row grid. The variation is the point; equal cells are just a card grid.

```html
<div class="grid gap-[var(--space-4)] md:grid-cols-6 lg:grid-cols-12">
  <div class="surface-card p-[var(--space-8)] lg:col-span-7 lg:row-span-2">
    <!-- Anchor cell: the strongest single story -->
  </div>
  <div class="surface-card p-[var(--space-6)] lg:col-span-5">
    <!-- Supporting metric -->
  </div>
  <div class="surface-card p-[var(--space-6)] lg:col-span-5">
    <!-- Supporting capability -->
  </div>
</div>
```

Guidance: 4-6 cells maximum. One anchor cell at roughly 2x the area of the others. Each cell carries one idea with a visual, not a paragraph. Cell content should bleed to the edge on at least one cell to break the uniformity.

## Feature split

Alternate `lg:order-*` between consecutive splits so the eye zigzags. Keep the copy column narrow (5/12) even when there is room for more; wide measure is what makes marketing copy feel like documentation.

```html
<div class="grid items-center gap-[var(--space-16)] lg:grid-cols-12">
  <div class="lg:col-span-5 lg:order-2">
    <h2 class="text-[length:var(--text-2xl)] font-semibold
               tracking-[var(--tracking-heading)] text-balance">…</h2>
    <p class="mt-[var(--space-4)] text-[var(--fg-secondary)]
              leading-[var(--leading-body)]">…</p>
  </div>
  <div class="lg:col-span-7 lg:order-1">…</div>
</div>
```

## Sticky-scroll deep dive

Pinned visual on one side, copy steps past it. The single best pattern for explaining a multi-stage process.

```html
<section class="grid lg:grid-cols-2 lg:gap-[var(--space-16)]">
  <div class="lg:sticky lg:top-24 lg:h-[80vh]">
    <!-- Visual swaps as steps activate -->
  </div>
  <div class="flex flex-col gap-[var(--section-y)]">
    <!-- One block per step, each tall enough to dwell -->
  </div>
</section>
```

Implement step activation with `IntersectionObserver` at `rootMargin: "-45% 0px -45% 0px"` so a step activates when it reaches the vertical center. Fall back to all-visible when JavaScript has not loaded.

## Navigation

- **Sticky, reactive to direction.** Hide on scroll down past ~120px, reveal on scroll up. Translate the header, never animate `height`.
- **Two states.** Transparent over the hero, then a glass surface with a bottom hairline once scrolled. Transition over 200ms.
- **Mega-menu on hover** for pointer devices only; the same content must be reachable by keyboard and on touch via click.
- Mobile menu: full-screen sheet, staggered item entrance, `inert` on the page behind it, focus trapped, `Escape` closes, scroll locked.
- Logo left, primary nav center or left-adjacent, actions right. Do not center the logo unless the brand demands it.

## Social proof

One substantial testimonial with a real name, role, company, and photo beats three short ones in a card row. If you must show several, use a single-row marquee of logos at low contrast rather than a grid of quote cards.

Metric strips work when the numbers are specific: `99.2%` reads as measured, `99%+` reads as marketing.

## Pricing

- Two or three tiers. Four is a decision-paralysis pattern unless tiers are genuinely distinct.
- Elevate one tier with a border in the accent color plus a small badge, not a different size that breaks alignment.
- Feature rows aligned across tiers so scanning down a column works.
- Show the annual/monthly toggle only if both exist. Animate the price with a number transition, not a hard swap.
- Currency and interval always adjacent to the number, at smaller size and `--fg-tertiary`.

## Footer

Dense and organized reads as substantial; sparse reads as unfinished.

- 4-5 columns of links with `--text-sm`, `--fg-secondary`, headings at `--text-xs` uppercase `--tracking-overline` in `--fg-tertiary`
- Legal row separated by a hairline, `--text-xs`
- Optional oversized wordmark at very low contrast as a closing gesture
- Never a full-width high-contrast CTA immediately above an identical CTA section

## Data-dense surfaces

For dashboards, review queues, and tables where the pattern above does not apply:

- `--text-sm` body, `--text-xs` for metadata, `tabular-nums` on every numeric column
- Row height 40-48px; denser than 36px hurts scanability on large screens
- Hairline row separators at `--border-subtle`, never full-strength borders on every cell
- Sticky header row with a glass background so content scrolls under it
- Right-align numbers, left-align text, never center either
- Status as a small pill: tinted background at ~12% chroma of the semantic hue, solid text at full strength, plus an icon
- Selection state uses a tinted background plus a 2px accent left border, not a checkbox alone
- Always design: loading skeleton matching real row heights, empty state with one action, error state with a retry

## Responsive checkpoints

Test at 360px, 768px, 1280px, 1920px, plus 2560px if the container is wide.

- Below 768px: single column, section padding drops to `--space-12`, display type drops two steps
- Horizontal scroll at any width is a defect. Check `overflow-x` on bleeding elements.
- Touch targets 44px minimum; increase spacing rather than shrinking type
- Sticky elements need `top` offsets that account for the mobile nav height
