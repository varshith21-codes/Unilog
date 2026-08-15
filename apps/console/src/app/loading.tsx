import { Skeleton } from "@/components/primitives";

/**
 * Route-level loading state.
 *
 * Block heights and the spacing between them mirror the overview's masthead, counter band and queue,
 * so the swap to real content does not shift the layout. That means it has to move when the page's
 * rhythm moves: the section tokens are referenced here rather than approximated, because a skeleton
 * that is close to the real spacing is a skeleton that visibly jumps.
 *
 * The column rules in the band are drawn too. They are the first thing the band renders and the last
 * thing a reader would expect to appear after the fact.
 */
export default function Loading() {
  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      {/* masthead: 7/5 split, `--spacing-section-lg` above and below */}
      <div className="grid gap-10 py-[var(--spacing-section-lg)] lg:grid-cols-12 lg:gap-12">
        <div className="flex flex-col gap-5 lg:col-span-7">
          <Skeleton className="h-3 w-52" rounded="xs" />
          <Skeleton className="h-12 w-full max-w-lg" rounded="lg" />
          <Skeleton className="h-4 w-full max-w-xl" rounded="xs" />
          <Skeleton className="h-4 w-3/4 max-w-lg" rounded="xs" />
          <div className="mt-4 flex gap-2">
            <Skeleton className="h-8 w-40" />
            <Skeleton className="h-8 w-28" />
          </div>
        </div>
        <div className="panel-raised flex flex-col gap-6 p-7 lg:col-span-5">
          <Skeleton className="h-3 w-24" rounded="xs" />
          <Skeleton className="h-10 w-28" rounded="lg" />
          <div className="hairline-t flex flex-col gap-6 pt-6">
            {[0, 1, 2, 3].map((row) => (
              <div key={row} className="flex flex-col gap-2">
                <Skeleton className="h-3 w-full" rounded="xs" />
                <Skeleton className="h-1 w-full" rounded="full" />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* counter band: same rules, same padding, same column dividers as `StatBand` */}
      <div className="hairline-t hairline-b grid grid-cols-2 gap-y-9 py-10 md:grid-cols-4">
        {[
          "pr-5 md:pr-6",
          "hairline-l pl-5 md:pl-6 md:pr-6",
          "pr-5 md:hairline-l md:pl-6 md:pr-6",
          "hairline-l pl-5 md:pl-6",
        ].map((column, index) => (
          <div key={index} className={`flex flex-col gap-2 ${column}`}>
            <Skeleton className="h-3 w-24" rounded="xs" />
            <Skeleton className="h-9 w-20" rounded="lg" />
            <Skeleton className="h-3 w-32" rounded="xs" />
          </div>
        ))}
      </div>

      {/* first section below the band, which carries the margin but no rule of its own */}
      <div className="mt-[var(--spacing-section)] flex flex-col gap-7">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-5 w-44" rounded="sm" />
          <Skeleton className="h-3.5 w-72" rounded="xs" />
        </div>

        <div className="panel flex flex-col overflow-hidden p-0">
          {Array.from({ length: 6 }, (_, row) => (
            <div
              key={row}
              className="hairline-b flex items-center gap-6 px-5 py-3.5 last:border-b-0"
            >
              <Skeleton className="h-4 w-28" rounded="xs" />
              <Skeleton className="h-4 w-14" rounded="xs" />
              <Skeleton className="h-1 flex-1" rounded="full" />
              <Skeleton className="h-4 w-12" rounded="xs" />
            </div>
          ))}
        </div>
      </div>

      <p className="sr-only" role="status">
        Loading
      </p>
    </div>
  );
}
