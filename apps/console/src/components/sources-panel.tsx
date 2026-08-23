import { AnchorIcon, CheckIcon, Overline, Panel, SectionHeading } from "@/components/primitives";
import type { RecordSource } from "@/lib/types";

/**
 * Where this record's data came from, as links a reviewer can open.
 *
 * The panel exists because a citation nobody can follow is an assertion. Every value in this system
 * already carries a quote and a document hash, which makes it *checkable by the machine* — this is
 * the half that makes it checkable by a person.
 *
 * **The tier is shown as prominently as the link, and that is the point.** A URL on the
 * manufacturer's own domain and a URL on some unrecognised host are both legitimate evidence, and
 * they are not worth the same. Only the first may be published into the delivery format's `MFR URL`
 * column. Rendering them identically would quietly upgrade the second, so the badge is not
 * decoration.
 *
 * Marketplaces and distributors never appear here. They are refused before the request is made, so
 * their bytes never entered the store — there is no path by which one could reach this panel.
 */
export function SourcesPanel({ sources }: { sources: RecordSource[] }) {
  if (sources.length === 0) return null;

  const retrieved = sources.filter((source) => source.tier !== "item_master");
  const manufacturer = retrieved.filter((source) => source.citable_as_manufacturer).length;

  return (
    <section aria-labelledby="sources-heading">
      <SectionHeading
        id="sources-heading"
        title="Sources"
        detail={
          retrieved.length === 0
            ? "Only the client's own item-master row. No manufacturer document has been retrieved for this part."
            : `${retrieved.length} retrieved document${retrieved.length === 1 ? "" : "s"}, ${manufacturer} on the manufacturer's own domain.`
        }
      />

      <Panel className="mt-6 overflow-hidden p-0">
        <ul>
          {sources.map((source) => (
            <li
              key={`${source.sha256}-${source.document_id}`}
              className="hairline-b px-6 py-4 last:border-b-0"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <TierPill source={source} />
                <CoveragePill source={source} />
                <span className="mono text-meta text-[var(--fg-quiet)]">
                  {source.doc_type.replace(/_/g, " ")}
                </span>
                {source.revision_label ? (
                  <span className="mono text-meta text-[var(--fg-quiet)]">
                    {source.revision_label}
                  </span>
                ) : null}
              </div>

              {source.tier === "item_master" ? (
                /*
                  Not a link. The item master is a file the client sent us, not a page on the web,
                  and rendering it as a link would imply an address a reviewer could open.
                */
                <p className="mono mt-2 break-all text-meta text-[var(--fg-tertiary)]">
                  {source.document_id}
                </p>
              ) : (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="mono mt-2 inline-flex items-start gap-1.5 break-all text-meta
                             text-[var(--accent)] underline-offset-2 hover:underline"
                >
                  <AnchorIcon className="mt-0.5 shrink-0" />
                  {source.url}
                </a>
              )}

              <p className="mono mt-2 text-meta text-[var(--fg-quiet)]">
                sha256 {source.sha256.slice(0, 16)}…
              </p>

              {source.license_note ? (
                /*
                  Shown rather than filed away. Crawled manufacturer content carries terms a
                  supplier-sent PDF does not, and the person about to republish an asset from it is
                  the person who needs to read them.
                */
                <p className="mt-2 max-w-[78ch] text-meta text-[var(--fg-tertiary)]">
                  {source.license_note}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </Panel>

      {retrieved.length > 0 && manufacturer === 0 ? (
        <p className="mt-4 max-w-[80ch] text-meta text-[var(--warn)]">
          None of these is on a declared manufacturer domain, so none may be published as the
          manufacturer&rsquo;s page. The values are still evidenced and still citable — they are just
          not citable as the manufacturer&rsquo;s own statement.
        </p>
      ) : null}
    </section>
  );
}

const TIER_LABEL: Record<string, string> = {
  manufacturer: "Manufacturer site",
  unknown: "Unrecognised host",
  item_master: "Client item master",
};

function TierPill({ source }: { source: RecordSource }) {
  if (source.citable_as_manufacturer) {
    return (
      <span className="pill pill-pass">
        <CheckIcon />
        {TIER_LABEL.manufacturer}
      </span>
    );
  }
  return (
    <span className={`pill ${source.tier === "item_master" ? "pill-accent" : "pill-warn"}`}>
      {TIER_LABEL[source.tier] ?? source.tier}
    </span>
  );
}

const COVERAGE_LABEL: Record<string, string> = {
  table: "listed in an ordering row",
  text: "mentioned in the text",
  row: "the row itself",
};

function CoveragePill({ source }: { source: RecordSource }) {
  const label = COVERAGE_LABEL[source.covers_this_sku];
  if (!label) return null;
  return (
    <>
      <Overline className="sr-only">Coverage</Overline>
      <span className="text-meta text-[var(--fg-tertiary)]">{label}</span>
    </>
  );
}
