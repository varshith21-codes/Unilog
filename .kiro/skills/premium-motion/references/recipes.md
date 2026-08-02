# Motion recipes

Working implementations. Each is reduced-motion aware and animates only `transform` and `opacity`.

## 1. Scroll reveal, CSS only

Preferred approach: off the main thread, no JavaScript, no observer bookkeeping.

```css
@keyframes reveal-up {
  from { opacity: 0; transform: translateY(24px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Visible by default so unsupported browsers show finished content */
.reveal { opacity: 1; }

@supports (animation-timeline: view()) {
  @media (prefers-reduced-motion: no-preference) {
    .reveal {
      animation: reveal-up linear both;
      animation-timeline: view();
      animation-range: entry 10% cover 35%;
    }
  }
}
```

`animation-range: entry 10% cover 35%` starts when the element is 10% into its entry and completes at 35% coverage, so the reveal finishes well before the element reaches center. Stagger by varying the range per child:

```css
.reveal:nth-child(2) { animation-range: entry 14% cover 40%; }
.reveal:nth-child(3) { animation-range: entry 18% cover 45%; }
```

## 2. Staggered list reveal, Motion

```tsx
"use client";
import { motion, useReducedMotion } from "motion/react";

export function RevealList({ items }: { items: { id: string; label: string }[] }) {
  const reduce = useReducedMotion();

  return (
    <motion.ul
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-15% 0px" }}
      variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}
    >
      {items.map((item) => (
        <motion.li
          key={item.id}
          variants={{
            hidden: { opacity: 0, y: reduce ? 0 : 12 },
            show: {
              opacity: 1,
              y: 0,
              transition: reduce
                ? { duration: 0.01 }
                : { duration: 0.5, ease: [0.16, 1, 0.3, 1] },
            },
          }}
        >
          {item.label}
        </motion.li>
      ))}
    </motion.ul>
  );
}
```

`once: true` and a negative viewport margin are what keep it from feeling twitchy.

## 3. Word-by-word headline

Splitting text breaks screen-reader flow unless you restore it. Keep an accessible copy and hide the decorative spans.

```tsx
"use client";
import { motion, useReducedMotion } from "motion/react";

export function AnimatedHeading({ text, className }: { text: string; className?: string }) {
  const reduce = useReducedMotion();
  if (reduce) return <h1 className={className}>{text}</h1>;

  return (
    <h1 className={className}>
      <span className="sr-only">{text}</span>
      <motion.span
        aria-hidden
        initial="hidden"
        animate="show"
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.025, delayChildren: 0.1 } } }}
        style={{ display: "inline-block" }}
      >
        {text.split(" ").map((w, i) => (
          <span key={i} style={{ display: "inline-block", overflow: "hidden", verticalAlign: "top" }}>
            <motion.span
              style={{ display: "inline-block" }}
              variants={{
                hidden: { y: "100%", opacity: 0 },
                show: { y: 0, opacity: 1, transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] } },
              }}
            >
              {w}&nbsp;
            </motion.span>
          </span>
        ))}
      </motion.span>
    </h1>
  );
}
```

The `overflow: hidden` wrapper is what produces the mask-reveal effect rather than a plain fade. Use per-character splitting only on short display text; on a paragraph it looks like a glitch.

## 4. Magnetic button

Pointer-only. Never ship this without the media query guard, or touch users get dead zones.

```tsx
"use client";
import { useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

export function MagneticButton({ children, strength = 0.25, ...props }: React.ComponentProps<typeof motion.button> & { strength?: number }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const reduce = useReducedMotion();

  const canMagnetize =
    !reduce &&
    typeof window !== "undefined" &&
    window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  function handleMove(e: React.MouseEvent) {
    if (!canMagnetize || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    setOffset({
      x: (e.clientX - (rect.left + rect.width / 2)) * strength,
      y: (e.clientY - (rect.top + rect.height / 2)) * strength,
    });
  }

  return (
    <motion.button
      ref={ref}
      onMouseMove={handleMove}
      onMouseLeave={() => setOffset({ x: 0, y: 0 })}
      animate={offset}
      transition={{ type: "spring", bounce: 0.2, duration: 0.5 }}
      {...props}
    >
      {children}
    </motion.button>
  );
}
```

Cap displacement around 8px. Beyond that the button stops feeling attached to the layout.

## 5. Scroll-direction-reactive header

Translate the header. Never animate `height`.

```tsx
"use client";
import { useState } from "react";
import { motion, useMotionValueEvent, useScroll } from "motion/react";

export function StickyHeader({ children }: { children: React.ReactNode }) {
  const { scrollY } = useScroll();
  const [hidden, setHidden] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useMotionValueEvent(scrollY, "change", (y) => {
    const previous = scrollY.getPrevious() ?? 0;
    setScrolled(y > 24);
    setHidden(y > previous && y > 120);
  });

  return (
    <motion.header
      animate={{ y: hidden ? "-100%" : 0 }}
      transition={{ duration: 0.28, ease: [0.25, 1, 0.5, 1] }}
      data-scrolled={scrolled}
      className="fixed inset-x-0 top-0 z-50 transition-colors duration-200
                 data-[scrolled=true]:border-b data-[scrolled=true]:border-[var(--border)]
                 data-[scrolled=true]:bg-[var(--background)]/80
                 data-[scrolled=true]:backdrop-blur-xl"
    >
      {children}
    </motion.header>
  );
}
```

## 6. Sticky-scroll stepper

Pinned visual, copy steps past it. The clearest way to explain a multi-stage process.

```tsx
"use client";
import { useEffect, useRef, useState } from "react";

export function StickySteps({ steps }: { steps: { id: string; title: string; body: string; visual: React.ReactNode }[] }) {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActive(Number((entry.target as HTMLElement).dataset.index));
          }
        }
      },
      { rootMargin: "-45% 0px -45% 0px" }
    );
    refs.current.forEach((el) => el && observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return (
    <section className="grid lg:grid-cols-2 lg:gap-16">
      <div className="lg:sticky lg:top-24 lg:h-[70vh]">
        {steps.map((s, i) => (
          <div
            key={s.id}
            className="transition-opacity duration-300"
            style={{ opacity: active === i ? 1 : 0, position: i === 0 ? "relative" : "absolute", inset: 0 }}
          >
            {s.visual}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-[clamp(4rem,10vh,9rem)]">
        {steps.map((s, i) => (
          <div
            key={s.id}
            data-index={i}
            ref={(el) => { refs.current[i] = el; }}
            className="transition-opacity duration-300"
            style={{ opacity: active === i ? 1 : 0.4 }}
          >
            <h3>{s.title}</h3>
            <p>{s.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
```

`rootMargin: "-45% 0px -45% 0px"` activates a step as it crosses the vertical center. Content is present in the DOM regardless, so it remains accessible without JavaScript.

## 7. Subtle parallax

```css
@keyframes drift {
  from { transform: translateY(-8%); }
  to   { transform: translateY(8%); }
}

@supports (animation-timeline: view()) {
  @media (prefers-reduced-motion: no-preference) {
    .parallax-bg {
      animation: drift linear both;
      animation-timeline: view();
      animation-range: cover;
    }
  }
}
```

Keep displacement within ±10%. Strong parallax reads as broken on trackpads and causes motion discomfort.

## 8. Infinite marquee

Duplicate the content and translate by exactly `-50%` so the loop is seamless.

```tsx
export function Marquee({ children }: { children: React.ReactNode }) {
  return (
    <div className="group relative overflow-hidden
                    [mask-image:linear-gradient(90deg,transparent,black_12%,black_88%,transparent)]">
      <div className="flex w-max animate-[marquee_32s_linear_infinite]
                      gap-16 group-hover:[animation-play-state:paused]
                      motion-reduce:animate-none">
        <div className="flex shrink-0 items-center gap-16">{children}</div>
        <div className="flex shrink-0 items-center gap-16" aria-hidden>{children}</div>
      </div>
    </div>
  );
}
```

The gradient mask on the edges is what stops it looking like a cheap ticker. Pause on hover is required; the duplicate must be `aria-hidden`.

## 9. Number counter

```tsx
"use client";
import { useEffect } from "react";
import { animate, useInView, useMotionValue, useTransform, motion, useReducedMotion } from "motion/react";
import { useRef } from "react";

export function Counter({ to, decimals = 0 }: { to: number; decimals?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-20%" });
  const count = useMotionValue(0);
  const text = useTransform(count, (v) => v.toFixed(decimals));
  const reduce = useReducedMotion();

  useEffect(() => {
    if (!inView) return;
    if (reduce) { count.set(to); return; }
    const controls = animate(count, to, { duration: 1.4, ease: [0.16, 1, 0.3, 1] });
    return () => controls.stop();
  }, [inView, to, reduce, count]);

  return <motion.span ref={ref} className="tabular-nums">{text}</motion.span>;
}
```

`tabular-nums` prevents the width jitter that otherwise makes counters look broken.

## 10. Route transition, Next.js App Router

```tsx
"use client";
import { AnimatePresence, motion } from "motion/react";
import { usePathname } from "next/navigation";

export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.main
        key={pathname}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.3, ease: [0.25, 1, 0.5, 1] }}
      >
        {children}
      </motion.main>
    </AnimatePresence>
  );
}
```

Keep route transitions under 400ms total including the exit. Anything longer makes navigation feel unresponsive. Move focus to the new main region on change so keyboard users are not stranded.

## 11. Layout animation for filtering and reordering

```tsx
<motion.div layout className="grid gap-4 md:grid-cols-3">
  <AnimatePresence mode="popLayout">
    {visible.map((item) => (
      <motion.article
        key={item.id}
        layout
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.96 }}
        transition={{ type: "spring", bounce: 0.15, duration: 0.4 }}
      >
        {item.title}
      </motion.article>
    ))}
  </AnimatePresence>
</motion.div>
```

The `layout` prop handles FLIP for you. Items sliding to new positions instead of jumping is one of the clearest signals of a carefully built interface.

## 12. Smooth scroll with Lenis

```tsx
"use client";
import { useEffect } from "react";
import Lenis from "lenis";

export function SmoothScroll() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const lenis = new Lenis({ duration: 1.1, smoothWheel: true });
    let frame = 0;
    const raf = (time: number) => { lenis.raf(time); frame = requestAnimationFrame(raf); };
    frame = requestAnimationFrame(raf);

    return () => { cancelAnimationFrame(frame); lenis.destroy(); };
  }, []);

  return null;
}
```

Keep `duration` at or below about 1.2. Higher values feel like lag rather than smoothness. Test alongside anchor links and focus scrolling; smooth-scroll libraries frequently break both.

## 13. Skeleton loading

Shimmer via `transform` on a gradient overlay, not by animating `background-position`.

```css
.skeleton {
  position: relative;
  overflow: hidden;
  background: var(--muted);
  border-radius: var(--radius-md);
}
.skeleton::after {
  content: "";
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, oklch(1 0 0 / 0.08), transparent);
  animation: shimmer 1.6s infinite;
}
@keyframes shimmer { to { transform: translateX(100%); } }

@media (prefers-reduced-motion: reduce) {
  .skeleton::after { animation: none; }
}
```

Skeletons must match the real content's dimensions. A skeleton that is a different height than what replaces it causes a layout shift, which is worse than a spinner.

## Verification checklist

Before considering any animation done:

1. DevTools Performance recording shows frames under 16ms during the animation.
2. Toggle OS reduced-motion and confirm the UI still works and reads correctly.
3. Tab through the animated region; focus is visible and never trapped or lost.
4. Test on a mid-range mobile device or with CPU throttled 4x.
5. Confirm no animated property is in the layout-triggering set.
6. Confirm reveals fire once, not on every scroll pass.
