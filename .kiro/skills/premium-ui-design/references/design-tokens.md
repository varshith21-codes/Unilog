# Design token system

Complete token layer. Copy, then retune hue and chroma to the brand. Every component value should resolve to something on this page.

## Why OKLCH

`oklch(L C H)` is perceptually uniform: equal lightness steps look equally spaced, unlike HSL where `hsl(60 100% 50%)` (yellow) is far brighter than `hsl(240 100% 50%)` (blue) at the same stated lightness. That property is what makes a generated scale look designed.

- **L** — lightness, `0` to `1`
- **C** — chroma (saturation), `0` to about `0.37` in practice
- **H** — hue angle, `0` to `360`

Scale construction rules:

1. Hold **H** constant across a scale.
2. Ramp **L** on a smooth curve from about `0.98` down to `0.18`.
3. Peak **C** at the middle of the scale and taper toward both ends. Light tints and dark shades with full chroma look artificial.

## Color primitives

```css
:root {
  /* Brand hue. Change this one number to rehue the product. */
  --hue-brand: 264;
  --hue-accent: 264;

  /* Neutral scale — tinted with brand hue, never pure gray */
  --neutral-50:  oklch(0.985 0.003 var(--hue-brand));
  --neutral-100: oklch(0.967 0.005 var(--hue-brand));
  --neutral-200: oklch(0.925 0.007 var(--hue-brand));
  --neutral-300: oklch(0.870 0.009 var(--hue-brand));
  --neutral-400: oklch(0.708 0.012 var(--hue-brand));
  --neutral-500: oklch(0.556 0.014 var(--hue-brand));
  --neutral-600: oklch(0.440 0.014 var(--hue-brand));
  --neutral-700: oklch(0.371 0.013 var(--hue-brand));
  --neutral-800: oklch(0.269 0.011 var(--hue-brand));
  --neutral-900: oklch(0.208 0.009 var(--hue-brand));
  --neutral-950: oklch(0.145 0.007 var(--hue-brand));

  /* Accent scale — chroma peaks at 500 */
  --accent-50:  oklch(0.977 0.014 var(--hue-accent));
  --accent-100: oklch(0.946 0.033 var(--hue-accent));
  --accent-200: oklch(0.902 0.063 var(--hue-accent));
  --accent-300: oklch(0.828 0.111 var(--hue-accent));
  --accent-400: oklch(0.714 0.163 var(--hue-accent));
  --accent-500: oklch(0.606 0.198 var(--hue-accent));
  --accent-600: oklch(0.541 0.190 var(--hue-accent));
  --accent-700: oklch(0.474 0.164 var(--hue-accent));
  --accent-800: oklch(0.404 0.132 var(--hue-accent));
  --accent-900: oklch(0.348 0.104 var(--hue-accent));

  /* Semantic — fixed hues, do not rehue with brand */
  --success: oklch(0.648 0.150 152);
  --warning: oklch(0.769 0.163  70);
  --danger:  oklch(0.586 0.222  27);
  --info:    oklch(0.623 0.170 245);
}
```

### Semantic aliases

Components reference these, never the primitives above.

```css
:root {
  --bg-canvas:      var(--neutral-50);
  --bg-surface:     oklch(1 0 0);
  --bg-raised:      oklch(1 0 0);
  --bg-sunken:      var(--neutral-100);
  --bg-inset:       var(--neutral-200);

  --fg-primary:     var(--neutral-950);
  --fg-secondary:   var(--neutral-600);
  --fg-tertiary:    var(--neutral-500);
  --fg-on-accent:   oklch(1 0 0);

  --border-subtle:  oklch(0 0 0 / 0.06);
  --border-default: oklch(0 0 0 / 0.10);
  --border-strong:  oklch(0 0 0 / 0.18);

  --ring:           var(--accent-500);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg-canvas:    var(--neutral-950);
    --bg-surface:   var(--neutral-900);
    --bg-raised:    var(--neutral-800);  /* lighter as it rises */
    --bg-sunken:    oklch(0.115 0.006 var(--hue-brand));
    --bg-inset:     oklch(0.100 0.005 var(--hue-brand));

    --fg-primary:   var(--neutral-100);
    --fg-secondary: var(--neutral-400);
    --fg-tertiary:  var(--neutral-500);

    --border-subtle:  oklch(1 0 0 / 0.06);
    --border-default: oklch(1 0 0 / 0.10);
    --border-strong:  oklch(1 0 0 / 0.18);

    /* Pull chroma back so accents stop vibrating on dark */
    --accent-500: oklch(0.646 0.170 var(--hue-accent));
  }
}
```

## Type scale

Fluid where it matters, fixed where it must be predictable.

```css
:root {
  --font-sans: "Inter Variable", ui-sans-serif, system-ui, sans-serif;
  --font-display: var(--font-sans);
  --font-mono: "JetBrains Mono Variable", ui-monospace, monospace;

  /* Fixed UI steps — 1.2 ratio */
  --text-2xs: 0.6875rem;  /* 11px  overlines only */
  --text-xs:  0.75rem;    /* 12px  metadata */
  --text-sm:  0.875rem;   /* 14px  dense UI, table cells */
  --text-base:1rem;       /* 16px  body floor */
  --text-md:  1.0625rem;  /* 17px  long-form body */
  --text-lg:  1.25rem;    /* 20px  lead paragraph */

  /* Fluid display steps — 1.333 ratio at the top end */
  --text-xl:   clamp(1.5rem,  1.2rem + 1.2vw, 1.953rem);
  --text-2xl:  clamp(1.875rem, 1.4rem + 2vw,  2.604rem);
  --text-3xl:  clamp(2.25rem,  1.5rem + 3.2vw, 3.472rem);
  --text-4xl:  clamp(2.75rem,  1.6rem + 5vw,  4.629rem);
  --text-5xl:  clamp(3.25rem,  1.5rem + 8vw,  6.173rem);

  /* Leading — inverse to size */
  --leading-none: 1;
  --leading-display: 1.08;
  --leading-heading: 1.22;
  --leading-body: 1.6;
  --leading-relaxed: 1.75;

  /* Tracking — inverse to size */
  --tracking-display: -0.035em;
  --tracking-heading: -0.018em;
  --tracking-body: 0em;
  --tracking-wide: 0.06em;
  --tracking-overline: 0.11em;

  --weight-normal: 400;
  --weight-medium: 500;
  --weight-semibold: 600;
  --weight-bold: 680;   /* variable-font axis value, not a keyword */

  --measure: 65ch;
}
```

Baseline defaults worth setting globally:

```css
html {
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
h1, h2, h3 { text-wrap: balance; }
p { text-wrap: pretty; max-width: var(--measure); }
[data-numeric], td, .tabular { font-variant-numeric: tabular-nums; }
```

## Spacing

4px base. Component padding is fixed; section padding is fluid.

```css
:root {
  --space-px: 1px;
  --space-0-5: 0.125rem; /*  2 */
  --space-1:   0.25rem;  /*  4 */
  --space-1-5: 0.375rem; /*  6 */
  --space-2:   0.5rem;   /*  8 */
  --space-3:   0.75rem;  /* 12 */
  --space-4:   1rem;     /* 16 */
  --space-5:   1.25rem;  /* 20 */
  --space-6:   1.5rem;   /* 24 */
  --space-8:   2rem;     /* 32 */
  --space-10:  2.5rem;   /* 40 */
  --space-12:  3rem;     /* 48 */
  --space-16:  4rem;     /* 64 */
  --space-20:  5rem;     /* 80 */
  --space-24:  6rem;     /* 96 */
  --space-32:  8rem;     /* 128 */

  /* Fluid section rhythm */
  --section-y: clamp(4rem, 10vh, 9rem);
  --section-y-lg: clamp(6rem, 16vh, 14rem);

  /* Container */
  --container: 1280px;
  --container-wide: 1440px;
  --container-prose: 46rem;
  --gutter: clamp(1rem, 4vw, 2.5rem);
}
```

## Radii

Pick one family and hold it. Mixing sharp and pill shapes without intent looks accidental.

```css
:root {
  --radius-xs: 0.25rem;
  --radius-sm: 0.375rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;
  --radius-2xl: 1.5rem;
  --radius-full: 9999px;
}
```

Nested radius rule: an inner radius should equal the outer radius minus the gap between them. A 16px card with 8px padding wants an 8px inner radius, not 16px.

## Elevation

Layered and tinted. Single-shadow elevation is the flattest-looking option available.

```css
:root {
  --shadow-color: var(--hue-brand) 20% 20%;

  --shadow-xs:
    0 1px 2px -1px oklch(0.2 0.02 var(--hue-brand) / 0.08);

  --shadow-sm:
    0 1px 2px -1px oklch(0.2 0.02 var(--hue-brand) / 0.08),
    0 2px 4px -2px oklch(0.2 0.02 var(--hue-brand) / 0.06);

  --shadow-md:
    0 1px 2px -1px oklch(0.2 0.02 var(--hue-brand) / 0.07),
    0 4px 8px -3px oklch(0.2 0.02 var(--hue-brand) / 0.07),
    0 8px 16px -6px oklch(0.2 0.02 var(--hue-brand) / 0.05);

  --shadow-lg:
    0 2px 4px -2px oklch(0.2 0.02 var(--hue-brand) / 0.06),
    0 8px 16px -6px oklch(0.2 0.02 var(--hue-brand) / 0.08),
    0 20px 32px -12px oklch(0.2 0.02 var(--hue-brand) / 0.08);

  --shadow-xl:
    0 4px 8px -4px oklch(0.2 0.02 var(--hue-brand) / 0.05),
    0 16px 32px -12px oklch(0.2 0.02 var(--hue-brand) / 0.10),
    0 32px 64px -24px oklch(0.2 0.02 var(--hue-brand) / 0.12);

  /* Lit top edge — reads as a physical surface */
  --shadow-inset-top: inset 0 1px 0 0 oklch(1 0 0 / 0.08);
}
```

## Surface recipes

```css
/* Standard card: border-led, shadow as a whisper */
.surface-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
}

/* Glass: blur plus hairline border, never blur alone */
.surface-glass {
  background: oklch(1 0 0 / 0.72);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid oklch(1 0 0 / 0.18);
  box-shadow: var(--shadow-md), var(--shadow-inset-top);
}

/* Gradient border via double background */
.surface-gradient-border {
  border: 1px solid transparent;
  border-radius: var(--radius-xl);
  background:
    linear-gradient(var(--bg-surface), var(--bg-surface)) padding-box,
    linear-gradient(150deg,
      oklch(1 0 0 / 0.22),
      oklch(1 0 0 / 0.04) 40%,
      oklch(1 0 0 / 0)) border-box;
}

/* Ambient glow behind hero content */
.glow-ambient::before {
  content: "";
  position: absolute;
  inset: -20% -10% auto -10%;
  height: 60%;
  background: radial-gradient(
    ellipse 60% 50% at 50% 0%,
    oklch(0.606 0.198 var(--hue-accent) / 0.18),
    transparent 70%
  );
  pointer-events: none;
  z-index: -1;
}
```

## Grain overlay

The highest-value texture per byte. Keep opacity low.

```css
.grain::after {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
  opacity: 0.025;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)'/%3E%3C/svg%3E");
}
```

## Focus ring

```css
:where(a, button, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid var(--ring);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
```

## Contrast targets

| Content | Minimum |
|---|---|
| Body text | 4.5:1 |
| Text 24px+, or 19px+ bold | 3:1 |
| Icons and control borders | 3:1 |
| Focus ring against both surfaces | 3:1 |
| Decorative only | none |

Verify with a contrast tool against the actual rendered pair. `--fg-secondary` on `--bg-sunken` is the pair that most often fails.
