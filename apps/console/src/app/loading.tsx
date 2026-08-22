import { Skeleton } from "@/components/primitives";

/**
 * Neutral route loading state shaped around the shared index-page geometry.
 * Route-specific content is not invented before the destination resolves.
 */
export default function Loading() {
  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <div aria-hidden className="page-header">
        <div className="page-header-grid">
          <div className="min-w-0">
            <Skeleton className="h-3 w-28" rounded="xs" />
            <Skeleton className="mt-4 h-12 w-full max-w-xl" rounded="lg" />
            <div className="mt-5 flex max-w-2xl flex-col gap-2.5">
              <Skeleton className="h-4 w-full" rounded="xs" />
              <Skeleton className="h-4 w-4/5" rounded="xs" />
            </div>
            <div className="mt-5 flex gap-3">
              <Skeleton className="h-5 w-28" rounded="full" />
              <Skeleton className="h-5 w-36" rounded="full" />
            </div>
          </div>
          <div className="page-actions">
            <Skeleton className="h-[var(--control-height)] w-36" />
          </div>
        </div>
      </div>

      <div aria-hidden className="hairline-t hairline-b grid grid-cols-2 gap-y-9 py-10 md:grid-cols-4">
        {[
          "pr-5 md:pr-6",
          "hairline-l pl-5 md:pl-6 md:pr-6",
          "pr-5 md:hairline-l md:pl-6 md:pr-6",
          "hairline-l pl-5 md:pl-6",
        ].map((column) => (
          <div key={column} className={`flex flex-col gap-2 ${column}`}>
            <Skeleton className="h-3 w-24" rounded="xs" />
            <Skeleton className="h-10 w-20" rounded="lg" />
            <Skeleton className="h-3 w-32 max-w-full" rounded="xs" />
          </div>
        ))}
      </div>

      <div aria-hidden className="mt-[var(--spacing-section)]">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-6 w-48" rounded="sm" />
          <Skeleton className="h-3.5 w-96 max-w-full" rounded="xs" />
        </div>
        <div className="panel mt-7 overflow-hidden">
          <div className="table-head grid grid-cols-[1fr_8rem_6rem] gap-6 px-5 py-3">
            <Skeleton className="h-3 w-24" rounded="xs" />
            <Skeleton className="h-3 w-16" rounded="xs" />
            <Skeleton className="h-3 w-12" rounded="xs" />
          </div>
          {Array.from({ length: 6 }, (_, row) => (
            <div
              key={row}
              className="hairline-b grid grid-cols-[minmax(0,1fr)_8rem_6rem] items-center gap-6 px-5 py-4 last:border-b-0"
            >
              <div className="flex min-w-0 flex-col gap-2">
                <Skeleton className="h-4 w-36 max-w-full" rounded="xs" />
                <Skeleton className="h-3 w-full max-w-md" rounded="xs" />
              </div>
              <Skeleton className="h-4 w-20" rounded="xs" />
              <Skeleton className="h-6 w-16" rounded="full" />
            </div>
          ))}
        </div>
      </div>

      <p className="sr-only" role="status">Loading workflow</p>
    </div>
  );
}
