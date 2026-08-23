import type { Metadata } from "next";

import { EnrichForm } from "@/components/enrich-form";
import { EmptyState, PageHeader, Panel } from "@/components/primitives";
import { loadEnrichLimits } from "@/lib/enrich";

export const metadata: Metadata = {
  title: "Enrich",
  description: "Enrich one product from a part number, a manufacturer, and a description or a link.",
};

export const dynamic = "force-dynamic";

/**
 * The single-SKU entry point, on its own address.
 *
 * A route rather than a panel on `/pipeline`, and that separation is a decision rather than layout
 * convenience. `/pipeline` deliberately has no Run button: its whole argument is that a stage card
 * lighting up is a *replay* of persisted output, and it spends most of its labelling saying so
 * because an animated sequence looks exactly like live work. This screen is the inverse — it really
 * does execute, and it really does cost money.
 *
 * Putting both on one page would mean a mode flag deciding which of two opposite claims the same
 * cards were making, which is the single worst place in this console to put a boolean. So the stage
 * cards are shared (`components/stage-card.tsx`) and the framing is not.
 *
 * Unlike the dashboards there is no offline fixture fallback. Those exist because a dashboard with no
 * data is still worth rendering; this page's entire function is "send this to the pipeline", and
 * showing a form that cannot complete would be worse than saying the service is down.
 */
export default async function EnrichPage() {
  const limits = await loadEnrichLimits();

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        eyebrow="Enrich / single product"
        title="Enrich one product from what you know about it"
        detail={
          limits.ok
            ? "A part number, a manufacturer, and either a description or a link to the datasheet. AXIOM classifies it, extracts what the source states, cites every value, and files the result alongside the rest of the catalogue."
            : "This screen runs the online pipeline against the live service. It stays unavailable rather than presenting a form that cannot complete."
        }
        meta={
          limits.ok ? (
            <>
              {/*
                The cost, in the page header. Every other screen in this console reads persisted
                output and is free; this is the only one that bills, and that belongs above the fold
                rather than discovered at the submit button.
              */}
              <span className="pill pill-accent">Online · real model calls</span>
              <span>
                {limits.data.model_calls_per_run.without_copy} calls per run
                {limits.data.model_calls_per_run.with_copy !==
                limits.data.model_calls_per_run.without_copy
                  ? `, ${limits.data.model_calls_per_run.with_copy} with copy`
                  : ""}
              </span>
              <span>{limits.data.concurrent_runs} run at a time</span>
            </>
          ) : (
            <span className="pill pill-warn">Service unavailable</span>
          )
        }
      />

      {limits.ok ? (
        <EnrichForm limits={limits.data} />
      ) : (
        <Panel className="overflow-hidden">
          <EmptyState
            kind="unmeasured"
            title="The enrichment service is not reachable"
            detail={limits.error}
          />
          <div className="hairline-t bg-[var(--surface-sunken)] px-6 py-5">
            <p className="text-meta text-[var(--fg-quiet)]">
              This endpoint needs AWS credentials, unlike the rest of the console. Start the API with
              a profile set, or run the same pipeline from the command line:
            </p>
            <pre className="mono scroll-x mt-3 text-meta leading-relaxed text-[var(--fg-secondary)]">{`$env:AWS_PROFILE = "axiom"
python -m uvicorn apps.api.main:app --port 8000

python scripts/run_pipeline.py https://example.com/spec.pdf \\
    --sku PDSH4816AF --save-session`}</pre>
          </div>
        </Panel>
      )}
    </div>
  );
}
