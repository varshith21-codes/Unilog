import { AlertIcon, CheckIcon, Overline, Quote } from "@/components/primitives";
import { percent, shortHash } from "@/lib/format";
import type { ManufacturerSpecification } from "@/lib/types";

interface ManufacturerSpecificationsProps {
  specifications: ManufacturerSpecification[];
  headingId?: string;
  title?: string;
}

function mappedAttributeCodeOf(specification: ManufacturerSpecification): string | null {
  const code = specification.mapped_attribute_code;
  return typeof code === "string" && code.trim().length > 0 ? code.trim() : null;
}

/**
 * The source-native layer: deliberately dense and technical, with hairline rows and monospace
 * provenance rather than cards. These rows are observations, not schema-approved attributes, so the
 * presentation never borrows review status or publishability language from the typed value queue.
 */
export function ManufacturerSpecifications({
  specifications,
  headingId = "manufacturer-specifications-heading",
  title = "Source specifications",
}: ManufacturerSpecificationsProps) {
  if (specifications.length === 0) return null;

  const mapped = specifications.filter(
    (specification) => mappedAttributeCodeOf(specification) !== null,
  ).length;

  return (
    <section aria-labelledby={headingId}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id={headingId} className="overline">
            {title}
          </h3>
          <p className="mt-2 max-w-[70ch] text-sm text-[var(--fg-tertiary)]">
            Every explicit label/value pair retained from the source. Quote verification and
            manufacturer-source authority are shown separately; known fields also flow through typed
            normalization.
          </p>
        </div>
        <p className="mono text-meta tabular-nums text-[var(--fg-quiet)]">
          {specifications.length} captured · {mapped} mapped · {specifications.length - mapped}{" "}
          source-only
        </p>
      </div>

      <dl className="mt-5 border-y border-[var(--hairline-strong)] divide-y divide-[var(--hairline-strong)]">
        {specifications.map((specification) => {
          const evidence = specification.evidence[0] ?? null;
          const mappedAttributeCode = mappedAttributeCodeOf(specification);
          const hasManufacturerAuthority = specification.citable_as_manufacturer === true;
          return (
            <div
              key={specification.specification_id}
              className="grid gap-3 py-4 sm:grid-cols-[minmax(10rem,0.72fr)_minmax(0,1.28fr)] sm:gap-6"
            >
              <dt className="min-w-0">
                <p className="text-sm font-medium [overflow-wrap:anywhere]">
                  {specification.label_raw}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="pill pill-quiet">
                    {mappedAttributeCode ?? "Source-only"}
                  </span>
                  <span className={`pill ${hasManufacturerAuthority ? "pill-accent" : "pill-warn"}`}>
                    {hasManufacturerAuthority ? <CheckIcon /> : <AlertIcon />}
                    {hasManufacturerAuthority ? "Manufacturer source" : "Publisher unverified"}
                  </span>
                  {evidence ? (
                    <span className={`pill ${evidence.quote_verified ? "pill-pass" : "pill-fail"}`}>
                      {evidence.quote_verified ? <CheckIcon /> : <AlertIcon />}
                      {evidence.quote_verified ? "Quote verified" : "Quote unverified"}
                    </span>
                  ) : null}
                </div>
              </dt>

              <dd className="min-w-0">
                <p className="text-base font-medium [overflow-wrap:anywhere]">
                  {specification.value_raw}
                </p>
                {evidence ? (
                  <details className="hairline-t mt-3 pt-3">
                    <summary className="disclosure text-meta">View source evidence</summary>
                    <div className="mt-3 border-l-2 border-[var(--accent)] pl-4">
                      <Quote>{evidence.quote}</Quote>
                    </div>
                    <div className="mono mt-3 flex flex-wrap gap-x-4 gap-y-1 text-meta text-[var(--fg-quiet)]">
                      <span>{evidence.document_id}</span>
                      <span>{evidence.page === null ? "page —" : `page ${evidence.page}`}</span>
                      {evidence.table_ref ? <span>{evidence.table_ref}</span> : null}
                      <span>sha256 {shortHash(evidence.document_sha256)}…</span>
                      {evidence.match_score !== null ? (
                        <span>match {percent(evidence.match_score, 0)}</span>
                      ) : null}
                    </div>
                  </details>
                ) : (
                  <Overline className="mt-3 text-[var(--fail)]">Evidence missing</Overline>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
