---
name: premium-motion
description: Add motion and interaction craft to web UI the way Framer, Linear, Stripe, and Apple do it - scroll-linked reveals, staggered entrances, spring physics, page transitions, magnetic hover, and micro-interactions that feel physical. Use when animating anything on the web, when a UI feels static, stiff, abrupt, or janky, when implementing scroll effects or parallax, when choosing easing curves and durations, when setting up Motion/Framer Motion, GSAP, Lenis, or CSS scroll-driven animations, or when animations need to respect reduced-motion and stay at 60fps.
license: MIT
compatibility: Framework-agnostic principles. Examples target CSS, Motion (motion.dev, formerly Framer Motion), GSAP, and Lenis in React/Next.js. Scroll-driven CSS animations need a Chromium 115+ / modern baseline with graceful fallback.
metadata:
  author: axiom-team
  version: "1.0.0"
---

# Premium motion

Motion is what separates a UI that looks designed from one that feels designed. It is also the fastest way to make a product feel cheap: a bounce that overshoots too far, a 600ms dropdown, or a page that stutters at 40fps reads worse than no animation at all.

The rule that governs everything below: **motion exists to explain a change, not to decorate one.** If an animation does not tell the user what just happened, where something came from, or what is now interactive, cut it.

## 1. Durations: shorter than instinct suggests

| Interaction | Duration | Notes |
|---|---|---|
| Hover, focus, color change | 120-180ms | Must feel instant |
| Button press | 80-120ms | Faster in than out |
| Tooltip, small popover | 150-200ms | |
| Dropdown, select, menu | 180-240ms | |
| Modal, drawer, sheet | 280-380ms | Large travel needs more time |
| Page or route transition | 350-500ms | Upper bound before it feels slow |
| Scroll-linked | not applicable | Driven by scroll position, not time |
| Ambient or looping | 3-20s | Must be ignorable |

Two asymmetry rules that most implementations get wrong:

- **Exits are faster than entrances**, typically 70-80% of the duration. The user has already decided; do not make them wait.
- **Larger travel needs longer duration.** A 4px shift at 300ms feels sluggish. A 400px drawer at 150ms feels violent.

## 2. Easing: never `linear`, rarely `ease`

`linear` looks mechanical. The CSS default `ease` is a weak curve that reads as generic. Use a defined set of curves as tokens.

```css
:root {
  /* Entrances, reveals: fast start, long settle */
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-out-expo:  cubic-bezier(0.16, 1, 0.3, 1);

  /* Exits: gentle start, decisive finish */
  --ease-in-quart:  cubic-bezier(0.5, 0, 0.75, 0);

  /* Moves between two on-screen states */
  --ease-in-out-quart: cubic-bezier(0.76, 0, 0.24, 1);

  /* Playful overshoot. Use on small elements only. */
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
}
```

Selection guide:

- **Entering the screen** → ease-out. The object arrives quickly then settles.
- **Leaving the screen** → ease-in. It accelerates away.
- **Moving between two visible positions** → ease-in-out.
- **Anything with weight or elasticity** → spring, not a bezier.

Duration sets how long; easing sets how the movement is distributed across it ([Motion easing docs](https://motion.dev/docs/easing-functions)). Motion ships `easeIn/Out/InOut`, `backIn/Out/InOut`, `circIn/Out/InOut`, `anticipate`, `steps`, and `cubicBezier`.

## 3. Springs for anything the user drags, toggles, or grabs

Springs derive duration from physics, so interrupted animations resolve naturally instead of snapping. Prefer the modern `bounce` + `duration` API over raw stiffness/damping:

```ts
// Motion (motion.dev)
const snappy   = { type: "spring", bounce: 0,    duration: 0.35 }; // no overshoot
const standard = { type: "spring", bounce: 0.2,  duration: 0.5  }; // default choice
const playful  = { type: "spring", bounce: 0.45, duration: 0.7  }; // small elements only
```

Use a spring for: drag release, layout shifts, toggles, sheets, anything gesture-driven. Use a tween for: opacity fades, color changes, scroll-linked values.

Overshoot on a large surface looks like a bug. Cap `bounce` at about `0.2` for anything bigger than a card.

## 4. Stagger is the single highest-value technique

A list that animates in as one block reads as a loading state. The same list staggered by 40ms per item reads as intentional.

- **40-60ms** between siblings in a list or grid
- **80-120ms** between major page sections
- **20-30ms** between words in a headline; **12-18ms** between characters (characters only for short display text)
- Cap total stagger at about **600ms**. For long lists, stagger the first 6-8 items and show the rest immediately.

Entrance recipe that works nearly everywhere: `opacity: 0 → 1` combined with `translateY(12px → 0)` over 500ms with `--ease-out-quart`, staggered 50ms. Resist larger travel; 40px slides look like a template.

## 5. Scroll: link, do not trigger

Prefer CSS scroll-driven animations. They run off the main thread, need no JavaScript, and scrub in both directions ([MDN: animation-timeline](https://developer.mozilla.org/en-US/docs/Web/CSS/animation-timeline)).

```css
@keyframes reveal {
  from { opacity: 0; transform: translateY(24px); }
  to   { opacity: 1; transform: translateY(0); }
}

.reveal {
  animation: reveal linear both;
  animation-timeline: view();
  animation-range: entry 10% cover 35%;
}

/* Progress bar tied to document scroll */
.progress {
  animation: grow linear both;
  animation-timeline: scroll(root block);
  transform-origin: left;
}
@keyframes grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
```

Guard with `@supports (animation-timeline: view())` and leave content visible by default so a browser without support shows a finished page rather than a blank one.

Scroll motion rules:

- **Parallax must be subtle.** Background at 0.85x scroll speed, foreground at 1.05x. Anything stronger induces nausea and breaks on trackpads.
- **Reveal once.** Elements that re-animate every time they scroll into view are irritating. Use `animation-fill-mode: both` with a one-way range, or unobserve after firing.
- **Never hijack scroll speed** without a strong reason. Smooth-scroll libraries like Lenis are acceptable; scroll-jacking that fights the user's input is not.
- **Pin sparingly.** One pinned section per page is impressive; three is exhausting.

Load `references/recipes.md` for working implementations: staggered reveals, text-by-word entrance, sticky-scroll steppers, magnetic buttons, marquees, page transitions, and number counters. Load `references/motion-tokens.md` for the complete token set and Motion variant library.

## 6. Micro-interactions that read as expensive

- **Multi-property hover.** Change background *and* border *and* a 1-2px `translateY`. Single-property hover feels flat.
- **Press feedback.** `scale(0.98)` plus `translateY(1px)` at 100ms. The absence of press feedback is why web buttons feel less satisfying than native ones.
- **Magnetic pull.** Buttons that drift 4-8px toward the cursor. Requires `@media (hover: hover) and (pointer: fine)`.
- **Optimistic UI.** Reflect the intended result immediately, reconcile after the response. Perceived speed beats actual speed.
- **Number transitions.** Animate digits when a metric changes rather than swapping text. Keep `tabular-nums` so nothing reflows.
- **Layout animation** (`layout` prop in Motion, FLIP in GSAP) for reordering, filtering, and expanding. Items sliding to new positions instead of jumping is the clearest "this was built carefully" signal.

## 7. Performance is non-negotiable

- **Animate `transform` and `opacity` only.** `width`, `height`, `top`, `left`, `margin`, and `padding` trigger layout on every frame. `filter` and `box-shadow` trigger paint; animate a pseudo-element's opacity instead.
- **`will-change` is a scalpel.** Apply before the animation, remove after. Leaving it on dozens of elements exhausts GPU memory and makes things slower.
- **Budget: 60fps, frames under 16ms.** Profile with DevTools Performance and check for green "Frames" bars, not vibes.
- **Cap concurrent animations.** More than ~20 simultaneously animating elements will drop frames on mid-range hardware.
- **Blur is expensive.** `backdrop-filter` on a large animating surface is a common cause of jank. Animate opacity of a pre-blurred layer instead.
- **Lazy-load heavy libraries.** GSAP plugins, Three.js, and Lottie should not sit in the critical bundle.

## 8. Accessibility

Wrap non-essential motion so it degrades to instant, not to broken:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

Better than blanket disabling: keep opacity fades, drop travel and parallax. Users with vestibular sensitivity object to movement, not to change.

```ts
const reduce = useReducedMotion();
const variants = {
  hidden: { opacity: 0, y: reduce ? 0 : 16 },
  show:   { opacity: 1, y: 0 },
};
```

Also required:

- Never convey information through motion alone.
- No flashing above 3Hz.
- Auto-playing carousels and marquees need a pause control, and must pause on hover and focus.
- Content must be readable and operable before animations run. Do not gate content on animation completion.
- Focus must not be lost during a transition; move focus deliberately on route change.

## 9. Restraint

- **One hero moment per page.** If everything animates, nothing is emphasized.
- **Interruptible always.** An animation the user cannot cancel by acting is a wall.
- **Never delay input feedback.** Entrance animations may take 500ms; a click response may not.
- **No animation on frequently repeated actions.** A 300ms flourish on a keystroke or a filter toggle becomes friction by the tenth use.
- **Match brand energy.** A financial dashboard and a sneaker launch do not share a motion vocabulary.

## Non-negotiables

1. `transform` and `opacity` only.
2. No `linear` easing except for scroll-linked and continuous rotation.
3. Exits faster than entrances.
4. `prefers-reduced-motion` honored in every animated component.
5. Pointer-only effects wrapped in `@media (hover: hover) and (pointer: fine)`.
6. 60fps verified by profiling, not assumed.
7. Reveals fire once.
8. `will-change` removed after use.
9. Input feedback never waits on animation.
10. One hero moment per page.

## Companion skills

- `premium-ui-design` for the visual token system these curves plug into.
- `ui-design-review` to audit motion quality before shipping.
