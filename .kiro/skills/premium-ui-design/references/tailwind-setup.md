# Tailwind CSS v4 and shadcn/ui wiring

How to expose the token system as Tailwind utilities so components never carry raw values.

Tailwind v4 is CSS-first: configuration lives in the stylesheet under `@theme`, not in `tailwind.config.js`. Any custom property defined in `@theme` generates matching utilities automatically.

## Install

```bash
npm install tailwindcss @tailwindcss/postcss postcss
```

```js
// postcss.config.mjs
export default { plugins: { "@tailwindcss/postcss": {} } };
```

## app/globals.css

```css
@import "tailwindcss";

/* Dark mode driven by a class so users can override the OS setting */
@custom-variant dark (&:where(.dark, .dark *));

@theme {
  /* ---- Fonts ---- */
  --font-sans: "InterVariable", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrainsMonoVariable", ui-monospace, monospace;

  /* ---- Brand hue ---- */
  --hue-brand: 264;

  /* ---- Neutrals: generates bg-neutral-*, text-neutral-*, border-neutral-* ---- */
  --color-neutral-50:  oklch(0.985 0.003 264);
  --color-neutral-100: oklch(0.967 0.005 264);
  --color-neutral-200: oklch(0.925 0.007 264);
  --color-neutral-300: oklch(0.870 0.009 264);
  --color-neutral-400: oklch(0.708 0.012 264);
  --color-neutral-500: oklch(0.556 0.014 264);
  --color-neutral-600: oklch(0.440 0.014 264);
  --color-neutral-700: oklch(0.371 0.013 264);
  --color-neutral-800: oklch(0.269 0.011 264);
  --color-neutral-900: oklch(0.208 0.009 264);
  --color-neutral-950: oklch(0.145 0.007 264);

  /* ---- Accent ---- */
  --color-accent-50:  oklch(0.977 0.014 264);
  --color-accent-100: oklch(0.946 0.033 264);
  --color-accent-200: oklch(0.902 0.063 264);
  --color-accent-300: oklch(0.828 0.111 264);
  --color-accent-400: oklch(0.714 0.163 264);
  --color-accent-500: oklch(0.606 0.198 264);
  --color-accent-600: oklch(0.541 0.190 264);
  --color-accent-700: oklch(0.474 0.164 264);
  --color-accent-800: oklch(0.404 0.132 264);
  --color-accent-900: oklch(0.348 0.104 264);

  /* ---- Semantic ---- */
  --color-success: oklch(0.648 0.150 152);
  --color-warning: oklch(0.769 0.163  70);
  --color-danger:  oklch(0.586 0.222  27);
  --color-info:    oklch(0.623 0.170 245);

  /* ---- Fluid display type: generates text-display-* ---- */
  --text-display-sm: clamp(1.875rem, 1.4rem + 2vw,   2.604rem);
  --text-display-md: clamp(2.25rem,  1.5rem + 3.2vw, 3.472rem);
  --text-display-lg: clamp(2.75rem,  1.6rem + 5vw,   4.629rem);
  --text-display-xl: clamp(3.25rem,  1.5rem + 8vw,   6.173rem);

  /* ---- Tracking ---- */
  --tracking-display: -0.035em;
  --tracking-heading: -0.018em;
  --tracking-overline: 0.11em;

  /* ---- Radii ---- */
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;
  --radius-2xl: 1.5rem;

  /* ---- Layered shadows: generates shadow-* ---- */
  --shadow-sm:
    0 1px 2px -1px oklch(0.2 0.02 264 / 0.08),
    0 2px 4px -2px oklch(0.2 0.02 264 / 0.06);
  --shadow-md:
    0 1px 2px -1px oklch(0.2 0.02 264 / 0.07),
    0 4px 8px -3px oklch(0.2 0.02 264 / 0.07),
    0 8px 16px -6px oklch(0.2 0.02 264 / 0.05);
  --shadow-lg:
    0 2px 4px -2px oklch(0.2 0.02 264 / 0.06),
    0 8px 16px -6px oklch(0.2 0.02 264 / 0.08),
    0 20px 32px -12px oklch(0.2 0.02 264 / 0.08);
  --shadow-xl:
    0 4px 8px -4px oklch(0.2 0.02 264 / 0.05),
    0 16px 32px -12px oklch(0.2 0.02 264 / 0.10),
    0 32px 64px -24px oklch(0.2 0.02 264 / 0.12);

  /* ---- Motion: generates ease-* and duration-* ---- */
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-out-expo:  cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in-out-quart: cubic-bezier(0.76, 0, 0.24, 1);
  --ease-spring:    cubic-bezier(0.34, 1.56, 0.64, 1);

  /* ---- Layout ---- */
  --container-content: 1280px;
  --container-wide: 1440px;
  --spacing-gutter: clamp(1rem, 4vw, 2.5rem);
  --spacing-section: clamp(4rem, 10vh, 9rem);
  --spacing-section-lg: clamp(6rem, 16vh, 14rem);
}

/* Semantic aliases consumed by components and shadcn/ui */
@layer base {
  :root {
    --background: var(--color-neutral-50);
    --foreground: var(--color-neutral-950);
    --card: oklch(1 0 0);
    --card-foreground: var(--color-neutral-950);
    --popover: oklch(1 0 0);
    --popover-foreground: var(--color-neutral-950);
    --primary: var(--color-accent-600);
    --primary-foreground: oklch(1 0 0);
    --secondary: var(--color-neutral-100);
    --secondary-foreground: var(--color-neutral-900);
    --muted: var(--color-neutral-100);
    --muted-foreground: var(--color-neutral-500);
    --destructive: var(--color-danger);
    --destructive-foreground: oklch(1 0 0);
    --border: oklch(0 0 0 / 0.10);
    --input: oklch(0 0 0 / 0.14);
    --ring: var(--color-accent-500);
  }

  .dark {
    --background: var(--color-neutral-950);
    --foreground: var(--color-neutral-100);
    --card: var(--color-neutral-900);
    --card-foreground: var(--color-neutral-100);
    --popover: var(--color-neutral-900);
    --popover-foreground: var(--color-neutral-100);
    --primary: var(--color-accent-500);
    --primary-foreground: var(--color-neutral-950);
    --secondary: var(--color-neutral-800);
    --secondary-foreground: var(--color-neutral-100);
    --muted: var(--color-neutral-800);
    --muted-foreground: var(--color-neutral-400);
    --border: oklch(1 0 0 / 0.10);
    --input: oklch(1 0 0 / 0.14);
    --ring: var(--color-accent-400);
  }

  * { border-color: var(--border); }

  body {
    background: var(--background);
    color: var(--foreground);
    -webkit-font-smoothing: antialiased;
  }

  h1, h2, h3 { text-wrap: balance; }
  p { text-wrap: pretty; }
  td, th, [data-numeric] { font-variant-numeric: tabular-nums; }

  :where(a, button, input, select, textarea, [tabindex]):focus-visible {
    outline: 2px solid var(--ring);
    outline-offset: 2px;
  }
}
```

Map shadcn/ui's expected names to yours so generated components inherit the system:

```css
@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
}
```

`@theme inline` matters here: it inlines the referenced value rather than emitting another indirection, which is what lets `.dark` overrides resolve correctly inside generated utilities.

## Reusable component classes

Keep long utility strings out of markup for anything used more than twice.

```css
@layer components {
  .container-page {
    margin-inline: auto;
    max-width: var(--container-content);
    padding-inline: var(--spacing-gutter);
  }

  .section {
    padding-block: var(--spacing-section);
  }

  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    height: 2.75rem;
    padding-inline: 1.125rem;
    border-radius: var(--radius-lg);
    font-size: 0.9375rem;
    font-weight: 500;
    transition:
      background-color 160ms var(--ease-out-quart),
      border-color 160ms var(--ease-out-quart),
      transform 160ms var(--ease-out-quart),
      box-shadow 160ms var(--ease-out-quart);
  }
  .btn:active { transform: translateY(1px) scale(0.99); }
  .btn:disabled { opacity: 0.5; pointer-events: none; }

  .btn-primary {
    background: var(--primary);
    color: var(--primary-foreground);
    box-shadow: var(--shadow-sm), inset 0 1px 0 0 oklch(1 0 0 / 0.14);
  }
  .btn-primary:hover { background: var(--color-accent-700); }

  .btn-ghost {
    background: transparent;
    color: var(--foreground);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { background: var(--secondary); }

  .surface-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius-xl);
    box-shadow: var(--shadow-sm);
  }
}
```

## Fonts in Next.js

Self-host variable fonts; do not load from a third-party CDN at runtime.

```ts
// app/layout.tsx
import localFont from "next/font/local";

const inter = localFont({
  src: "./fonts/InterVariable.woff2",
  variable: "--font-sans",
  display: "swap",
  weight: "100 900",
});
```

Apply `inter.variable` to `<html>`. `next/font` self-hosts, preloads, and generates a size-adjusted fallback, which removes the layout shift that otherwise ships with a custom typeface.

## Verification

```bash
npx tsc --noEmit
npm run lint
npm run build
```

Then check in the browser: toggle `.dark` on `<html>`, tab through every interactive element to confirm focus rings, and resize to 360px to confirm no horizontal scroll.
