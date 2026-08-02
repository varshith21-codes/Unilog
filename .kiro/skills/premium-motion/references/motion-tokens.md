# Motion tokens and variant library

Single source of truth for curves, durations, and reusable Motion variants. Components reference tokens, never raw numbers.

## CSS tokens

```css
:root {
  /* ---- Easing ---- */
  --ease-out-quart:    cubic-bezier(0.25, 1, 0.5, 1);
  --ease-out-expo:     cubic-bezier(0.16, 1, 0.3, 1);
  --ease-out-circ:     cubic-bezier(0, 0.55, 0.45, 1);
  --ease-in-quart:     cubic-bezier(0.5, 0, 0.75, 0);
  --ease-in-expo:      cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out-quart: cubic-bezier(0.76, 0, 0.24, 1);
  --ease-in-out-expo:  cubic-bezier(0.87, 0, 0.13, 1);
  --ease-spring:       cubic-bezier(0.34, 1.56, 0.64, 1);
  --ease-anticipate:   cubic-bezier(0.68, -0.55, 0.27, 1.55);

  /* ---- Duration ---- */
  --duration-instant: 80ms;
  --duration-fast:    140ms;
  --duration-base:    200ms;
  --duration-slow:    300ms;
  --duration-slower:  450ms;
  --duration-slowest: 650ms;

  /* ---- Stagger ---- */
  --stagger-tight:  30ms;
  --stagger-base:   50ms;
  --stagger-loose:  90ms;

  /* ---- Composed transitions ---- */
  --transition-hover:
    background-color var(--duration-fast) var(--ease-out-quart),
    border-color     var(--duration-fast) var(--ease-out-quart),
    color            var(--duration-fast) var(--ease-out-quart),
    transform        var(--duration-fast) var(--ease-out-quart),
    box-shadow       var(--duration-fast) var(--ease-out-quart);

  --transition-enter: opacity var(--duration-slower) var(--ease-out-quart),
                      transform var(--duration-slower) var(--ease-out-quart);
  --transition-exit:  opacity var(--duration-base) var(--ease-in-quart),
                      transform var(--duration-base) var(--ease-in-quart);
}
```

## Duration reference

| Token | Value | Use |
|---|---|---|
| `instant` | 80ms | Press feedback |
| `fast` | 140ms | Hover, focus, color |
| `base` | 200ms | Tooltips, dropdowns |
| `slow` | 300ms | Popovers, accordions |
| `slower` | 450ms | Modals, drawers, entrances |
| `slowest` | 650ms | Full-page or hero sequences |

Exits use one step down from their entrance token.

## Spring presets (Motion)

```ts
// lib/motion.ts
export const spring = {
  /** No overshoot. Large surfaces, drawers, modals. */
  snappy:   { type: "spring", bounce: 0,    duration: 0.35 },
  /** Default for most interactive movement. */
  standard: { type: "spring", bounce: 0.2,  duration: 0.5  },
  /** Small elements only: toggles, badges, icons. */
  playful:  { type: "spring", bounce: 0.45, duration: 0.7  },
  /** Gesture release and drag settle. */
  drag:     { type: "spring", bounce: 0.15, duration: 0.4  },
} as const;

export const tween = {
  fast: { duration: 0.14, ease: [0.25, 1, 0.5, 1] },
  base: { duration: 0.2,  ease: [0.25, 1, 0.5, 1] },
  enter:{ duration: 0.45, ease: [0.16, 1, 0.3, 1] },
  exit: { duration: 0.25, ease: [0.5, 0, 0.75, 0] },
} as const;
```

## Variant library

```ts
// lib/variants.ts
import type { Variants } from "motion/react";

/** Parent that staggers its children. Children use `fadeUp` etc. */
export const stagger = (delayChildren = 0, staggerChildren = 0.05): Variants => ({
  hidden: {},
  show: {
    transition: { delayChildren, staggerChildren },
  },
});

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  show:   { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] } },
};

export const fade: Variants = {
  hidden: { opacity: 0 },
  show:   { opacity: 1, transition: { duration: 0.4, ease: [0.25, 1, 0.5, 1] } },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.96 },
  show:   { opacity: 1, scale: 1, transition: { type: "spring", bounce: 0.2, duration: 0.5 } },
};

/** Drawers and sheets. Pass a direction. */
export const slideIn = (from: "left" | "right" | "top" | "bottom"): Variants => {
  const axis = from === "left" || from === "right" ? "x" : "y";
  const sign = from === "left" || from === "top" ? -1 : 1;
  return {
    hidden: { [axis]: sign * 100 + "%", opacity: 0 },
    show:   { [axis]: 0, opacity: 1, transition: { type: "spring", bounce: 0, duration: 0.4 } },
    exit:   { [axis]: sign * 100 + "%", opacity: 0, transition: { duration: 0.25, ease: [0.5, 0, 0.75, 0] } },
  };
};

/** Word-by-word headline entrance. */
export const wordStagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.025, delayChildren: 0.1 } },
};

export const word: Variants = {
  hidden: { opacity: 0, y: "0.4em" },
  show:   { opacity: 1, y: 0, transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] } },
};
```

Usage:

```tsx
<motion.ul
  variants={stagger(0.1, 0.05)}
  initial="hidden"
  whileInView="show"
  viewport={{ once: true, margin: "-15%" }}
>
  {items.map((item) => (
    <motion.li key={item.id} variants={fadeUp}>{item.label}</motion.li>
  ))}
</motion.ul>
```

`viewport={{ once: true }}` is important. Re-animating on every scroll pass is the most common motion mistake.

## Reduced-motion aware variants

```ts
import { useReducedMotion } from "motion/react";

export function useFadeUp() {
  const reduce = useReducedMotion();
  return {
    hidden: { opacity: 0, y: reduce ? 0 : 12 },
    show: {
      opacity: 1,
      y: 0,
      transition: reduce
        ? { duration: 0.01 }
        : { duration: 0.5, ease: [0.16, 1, 0.3, 1] },
    },
  } satisfies Variants;
}
```

Keep the opacity change and drop the travel. Users with vestibular sensitivity object to movement, not to state change.

## Tailwind v4 exposure

```css
@theme {
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-out-expo:  cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in-quart:  cubic-bezier(0.5, 0, 0.75, 0);
  --ease-spring:    cubic-bezier(0.34, 1.56, 0.64, 1);

  --animate-fade-up: fade-up 0.5s var(--ease-out-expo) both;
  --animate-marquee: marquee 32s linear infinite;
}

@keyframes fade-up {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes marquee {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
```

This yields `ease-out-quart`, `ease-spring`, `animate-fade-up`, and `animate-marquee` utilities.

## Library selection

| Need | Use | Why |
|---|---|---|
| React component animation, layout shifts, gestures | **Motion** (`motion` package) | Declarative, spring-native, `layout` prop handles FLIP automatically |
| Complex multi-step timelines, pinning, vanilla or Astro | **GSAP** + ScrollTrigger | Timeline sequencing and scroll orchestration remain best in class |
| Simple reveals and parallax | **CSS scroll-driven animations** | Off main thread, zero JavaScript, scrubs both ways |
| Smooth scroll context | **Lenis** | Lightweight; do not combine with CSS scroll-driven timelines without testing |
| Text splitting | **SplitType** or manual spans | Preserve the original text for screen readers |
| 3D | **React Three Fiber** | Lazy-load it; never in the critical bundle |

Do not stack Motion and GSAP for the same effect. Pick one per concern.

## Bundle discipline

- Import `motion/react` selectively; avoid barrel-importing an entire animation suite.
- Register GSAP plugins only where used, inside a `useEffect` or dynamic import.
- Lottie files are frequently larger than the animation is worth. Prefer CSS or SVG for anything under moderate complexity.
- Verify with `npx next build` and check the reported route bundle sizes before and after.
