import type { Metadata } from "next";

import { DeliveryUpload } from "@/components/delivery-upload";
import { EmptyState, PageHeader, Panel } from "@/components/primitives";
import { loadDeliveryFormat } from "@/lib/delivery";

export const metadata: Metadata = {
  title: "Publish",
  description: "Prepare an evidence-backed item master for the contracted delivery format.",
};

export const dynamic = "force-dynamic";

export default async function DeliveryPage() {
  const contract = await loadDeliveryFormat();

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        eyebrow="Publish / delivery studio"
        title="Prepare a contracted catalog delivery"
        detail={
          contract.ok
            ? `Bring an item master and optional manufacturer documents. AXIOM returns the ${contract.data.columns}-column ${contract.data.format} deliverable with populated, blank, and withheld cells kept distinct.`
            : "The delivery studio reads its schema and limits from the live export service. It stays unavailable rather than presenting an upload flow that cannot complete."
        }
        meta={
          contract.ok ? (
            <>
              <span className="pill pill-quiet">{contract.data.format}</span>
              <span>{contract.data.columns} contracted columns</span>
              <span>Join on <span className="mono">{contract.data.join_key}</span></span>
              {contract.data.populated_in_ground_truth !== null ? (
                <span>{contract.data.populated_in_ground_truth} populated in ground truth</span>
              ) : null}
            </>
          ) : (
            <span className="pill pill-warn">Service unavailable</span>
          )
        }
      />

      {contract.ok ? (
        <DeliveryUpload contract={contract.data} />
      ) : (
        <Panel className="overflow-hidden">
          <EmptyState
            kind="unmeasured"
            title="The Publish service is not reachable"
            detail={contract.error}
          />
          <div className="hairline-t bg-[var(--surface-sunken)] px-6 py-5">
            <p className="text-meta text-[var(--fg-quiet)]">
              Start the API, or run the same export from the command line:
            </p>
            <pre className="mono scroll-x mt-3 text-meta leading-relaxed text-[var(--fg-secondary)]">{`python -m uvicorn apps.api.main:app --port 8000

python scripts/export_delivery.py "Unihack_ Sample Dataset - Input.csv" \\
    --out data/delivery --xlsx`}</pre>
          </div>
        </Panel>
      )}
    </div>
  );
}
