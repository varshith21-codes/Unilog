import Link from "next/link";

import { ArrowIcon, Overline } from "@/components/primitives";

export const metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[70dvh] max-w-[var(--container-prose)] flex-col justify-center px-[var(--spacing-gutter)] py-20">
      <Overline>404</Overline>
      <h1 className="mt-4 text-display font-medium tracking-[var(--tracking-display)]">
        No such record
      </h1>
      <p className="mt-5 max-w-[52ch] text-body text-[var(--fg-secondary)]">
        That SKU is not in the current dataset. The console reads a fixture generated from the
        pipeline, so a SKU only appears here once it has been through a run.
      </p>
      <div className="mt-9 flex flex-wrap gap-2">
        <Link href="/" className="btn btn-primary">
          Overview
          <ArrowIcon />
        </Link>
        <Link href="/review" className="btn btn-quiet">
          Review queue
        </Link>
      </div>
    </div>
  );
}
