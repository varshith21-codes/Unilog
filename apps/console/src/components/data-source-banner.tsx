import { AlertIcon } from "@/components/primitives";
import { API_BASE, loadDataset } from "@/lib/data";

/**
 * A page-level warning when the console is not showing live pipeline output.
 *
 * This sits in the layout rather than on individual screens because the failure it describes is
 * global: if the API is unreachable, *every* number on *every* page came from the checked-in
 * fixture, whose model responses are hand-seeded rather than produced by a real model call.
 *
 * Putting it only on the overview — where the provenance footer already lives — would leave a
 * reviewer on `/review/BA-100-075` with no way to tell that the values in front of them were
 * authored rather than extracted, and no explanation for why their decisions will not save.
 * That is precisely the confusion this whole project exists to eliminate, so it would be a
 * strange thing to ship in the tool itself.
 *
 * Renders nothing on the happy path.
 */
export async function DataSourceBanner() {
  const { meta } = await loadDataset();
  if (meta.live) return null;

  return (
    <div
      role="status"
      className="border-b border-[color-mix(in_oklab,var(--warn)_35%,transparent)]
                 bg-[color-mix(in_oklab,var(--warn)_12%,var(--canvas))]"
    >
      <div
        className="mx-auto flex max-w-[var(--container-shell)] items-start gap-2.5
                   px-[var(--spacing-gutter)] py-2.5"
      >
        <AlertIcon className="mt-0.5 shrink-0 text-[var(--warn)]" />
        <p className="text-meta text-[var(--fg-secondary)]">
          <span className="font-medium text-[var(--fg)]">Offline fixture.</span> The AXIOM API at{" "}
          <span className="mono">{API_BASE}</span> is unreachable, so these values come from a
          checked-in fixture whose model responses are hand-seeded. Review decisions cannot be
          saved. Start it with{" "}
          <span className="mono">python -m uvicorn apps.api.main:app --port 8000</span>.
        </p>
      </div>
    </div>
  );
}
