import Link from "next/link";

import { ArrowIcon, PageHeader } from "@/components/primitives";

export const metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <div className="mx-auto min-h-[70dvh] max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        compact
        eyebrow="404"
        title="Page not found"
        detail="The requested address is unavailable or may have moved. Return to the workflow overview or continue in Resolve."
        actions={
          <>
            <Link href="/" className="btn btn-primary">
              Workflow overview
              <ArrowIcon />
            </Link>
            <Link href="/review" className="btn btn-quiet">
              Open Resolve
            </Link>
          </>
        }
      />
    </div>
  );
}
