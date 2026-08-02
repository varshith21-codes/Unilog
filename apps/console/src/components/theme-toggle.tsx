"use client";

/**
 * Theme switch.
 *
 * Dark mode is a separate design here, not an inversion: surfaces get lighter as they rise
 * and accent chroma drops. The initial class is set by an inline script in the document head
 * (see `layout.tsx`) before first paint.
 *
 * Deliberately stateless. An earlier version tracked the resolved theme in React state,
 * which meant the first client render always drew the light-mode icon and then corrected
 * itself — reintroducing exactly the flash the head script exists to prevent. Selecting the
 * glyph with the `dark:` variant instead means CSS resolves it on the first paint, with no
 * state, no effect, and no hydration mismatch.
 */
export function ThemeToggle() {
  function toggle() {
    const root = document.documentElement;
    const next = root.classList.toggle("dark") ? "dark" : "light";
    try {
      window.localStorage.setItem("axiom-theme", next);
    } catch {
      // Private browsing or blocked storage. The class is already applied; the preference
      // simply will not survive a reload, which is an acceptable degradation.
    }
  }

  return (
    <button
      type="button"
      className="btn btn-bare icon-target size-8 px-0"
      onClick={toggle}
      aria-label="Toggle dark mode"
      title="Toggle dark mode"
    >
      {/* Sun: shown in dark mode, where the action is to return to light. */}
      <svg
        viewBox="0 0 16 16"
        aria-hidden
        fill="none"
        className="hidden size-4 dark:block"
      >
        <circle cx="8" cy="8" r="3.1" stroke="currentColor" strokeWidth="1.3" />
        <path
          d="M8 1.6v1.5M8 12.9v1.5M1.6 8h1.5M12.9 8h1.5M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
      </svg>

      {/* Moon: shown in light mode. */}
      <svg viewBox="0 0 16 16" aria-hidden fill="none" className="size-4 dark:hidden">
        <path
          d="M13.4 9.6A5.8 5.8 0 0 1 6.4 2.6a5.9 5.9 0 1 0 7 7Z"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
