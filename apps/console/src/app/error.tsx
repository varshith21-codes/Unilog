"use client";

import { useEffect } from "react";

import { AlertIcon, Overline } from "@/components/primitives";

/**
 * Route-level error boundary.
 *
 * States what failed, why it plausibly failed, and offers a retry. The most likely cause in
 * this app is a missing or stale fixture, so that is named explicitly rather than left to a
 * generic apology.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto flex min-h-[70dvh] max-w-[var(--container-prose)] flex-col justify-center px-[var(--spacing-gutter)] py-20">
      <div className="flex items-center gap-2">
        <AlertIcon className="text-[var(--fail)]" />
        <Overline>Error</Overline>
      </div>

      <h1 className="mt-4 text-display font-medium tracking-[var(--tracking-display)]">
        This screen could not load
      </h1>

      <p className="mt-5 max-w-[56ch] text-body text-[var(--fg-secondary)]">
        The most common cause is a missing or out-of-date console fixture. Regenerate it from
        the pipeline, then reload:
      </p>

      <p className="mono mt-4 rounded-md bg-[var(--surface-inset)] px-3.5 py-2.5 text-[var(--fg-secondary)]">
        python scripts/export_console_fixture.py
      </p>

      {error.message ? (
        <p className="mono mt-5 break-words text-[var(--fg-quiet)]">{error.message}</p>
      ) : null}
      {error.digest ? (
        <p className="mono mt-1 text-[var(--fg-quiet)]">digest {error.digest}</p>
      ) : null}

      <div className="mt-9">
        <button type="button" className="btn btn-primary" onClick={reset}>
          Try again
        </button>
      </div>
    </div>
  );
}
