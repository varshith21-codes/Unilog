"use client";

import { useEffect } from "react";

import { AlertIcon, Overline, PageHeader, Panel } from "@/components/primitives";

/**
 * Route-level error boundary.
 *
 * Keeps the user inside the workflow shell, names the likely fixture failure, and preserves the
 * framework retry path through `reset`.
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
    <div className="mx-auto min-h-[70dvh] max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        compact
        eyebrow="Workflow error"
        title="This workflow could not load"
        detail="The route stopped before its workspace was ready. Retry the request, or refresh the console fixture if the failure persists."
        actions={
          <button type="button" className="btn btn-primary" onClick={reset}>
            Try again
          </button>
        }
      />

      <Panel raised className="max-w-[var(--container-prose)] p-6">
        <div className="flex items-center gap-2">
          <AlertIcon className="text-[var(--fail)]" />
          <Overline>Fixture recovery</Overline>
        </div>
        <p className="mt-4 max-w-[56ch] text-body text-[var(--fg-secondary)]">
          A missing or out-of-date console fixture is the most common cause. Regenerate it, then
          retry the workflow.
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
      </Panel>
    </div>
  );
}
