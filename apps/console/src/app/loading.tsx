import { Skeleton } from "@/components/primitives";

/**
 * Route-level loading state.
 *
 * Block heights mirror the masthead and table they stand in for, so the swap to real
 * content does not shift the layout.
 */
export default function Loading() {
  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <div className="grid gap-10 py-14 lg:grid-cols-12 lg:gap-12 lg:py-20">
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
        <div className="panel flex flex-col gap-6 p-7 lg:col-span-5">
          <Skeleton className="h-3 w-24" rounded="xs" />
          <Skeleton className="h-10 w-28" rounded="lg" />
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="flex flex-col gap-2">
              <Skeleton className="h-3 w-full" rounded="xs" />
              <Skeleton className="h-1 w-full" rounded="full" />
            </div>
          ))}
        </div>
      </div>

      <div className="hairline-t hairline-b grid grid-cols-2 gap-x-6 gap-y-9 py-9 md:grid-cols-4">
        {[0, 1, 2, 3].map((stat) => (
          <div key={stat} className="flex flex-col gap-2">
            <Skeleton className="h-3 w-24" rounded="xs" />
            <Skeleton className="h-9 w-20" rounded="lg" />
            <Skeleton className="h-3 w-32" rounded="xs" />
          </div>
        ))}
      </div>

      <div className="panel mt-14 flex flex-col gap-0 overflow-hidden p-0">
        {Array.from({ length: 6 }, (_, row) => (
          <div key={row} className="hairline-b flex items-center gap-6 px-5 py-3.5 last:border-b-0">
            <Skeleton className="h-4 w-28" rounded="xs" />
            <Skeleton className="h-4 w-14" rounded="xs" />
            <Skeleton className="h-1 flex-1" rounded="full" />
            <Skeleton className="h-4 w-12" rounded="xs" />
          </div>
        ))}
      </div>

      <p className="sr-only" role="status">
        Loading
      </p>
    </div>
  );
}
