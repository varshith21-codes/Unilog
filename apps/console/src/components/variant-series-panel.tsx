import Link from "next/link";

import { ArrowIcon, Overline, Panel, SectionHeading } from "@/components/primitives";
import type { VariantGroup, VariantMember } from "@/lib/data";
import { humanise, tableRef } from "@/lib/format";
import type { SkuBundle } from "@/lib/types";

/**
 * Variant explosion, made auditable.
 *
 * One ordering table on one datasheet becomes N orderable products. That claim invites an obvious
 * and correct suspicion — did five products get generated, or did one product get copied five
 * times? — and this panel exists to answer it with the records themselves rather than with prose.
 *
 * The table below is **not** the datasheet's table. It is the set of product records the pipeline
 * produced, arranged over the attributes the ordering table supplied, each cell showing the exact
 * table cell it was read from. If the pipeline had copied one product five times, every column here
 * would be identical and the cell references would all point at the same row.
 *
 * The three counts on the right are the rest of the answer. Values split three ways: read from this
 * variant's own row, inherited from the shared series specification, or withheld because the source
 * states them only for another size. That last one is the subtle case and the reason this is worth
 * showing — a torque figure printed once, for the half-inch valve, must not be inherited by four
 * larger valves that would each then carry a precisely-cited wrong number.
 */
export function VariantSeriesPanel({ group }: { group: VariantGroup }) {
  const axis = axisCodes(group.members);
  const totals = {
    fromOwnRow: sum(group.members, (member) => member.fromOwnRow),
    inherited: sum(group.members, (member) => member.inherited),
    inapplicable: sum(group.members, (member) => member.inapplicable),
  };

  return (
    <section>
      <SectionHeading
        title="Variant series"
        detail={
          <>
            One ordering table became {group.members.length}{" "}
            {group.members.length === 1 ? "product record" : "product records"}. Every cell below
            cites the table cell it was read from.
          </>
        }
        action={
          <span className="pill pill-accent">
            {group.members.length} from 1 table
          </span>
        }
      />

      {/*
        A series whose reference record was never persisted is still a real series, and saying so
        beats listing a partial family as if it were complete.
      */}
      {group.referencePresent ? null : (
        <Panel className="mt-6 border-l-2 border-l-[var(--warn)] p-6">
          <p className="text-sm text-[var(--fg-secondary)]">
            <span className="font-medium text-[var(--fg)]">Reference record absent.</span> These
            variants were exploded from{" "}
            <span className="mono">{group.seriesSku}</span>, whose own record is not in this
            catalogue. The shared specification below was inherited from it, so the series is
            complete as far as these products go — but the record the values were read from is not
            here to check them against.
          </p>
        </Panel>
      )}

      <Panel className="scroll-x mt-6 overflow-hidden p-0">
        <table className="w-full min-w-[56rem] border-collapse text-sm">
          <caption className="sr-only">
            Products in the {group.seriesSku} series, the attributes the ordering table supplied for
            each, and where each variant&rsquo;s values came from
          </caption>
          <thead>
            <tr className="hairline-b bg-[var(--surface-sunken)]">
              <th scope="col" className="px-5 py-3 text-left font-medium">
                SKU
              </th>
              {axis.map((code) => (
                <th key={code} scope="col" className="px-5 py-3 text-left font-medium">
                  {humanise(code)}
                </th>
              ))}
              <th scope="col" className="px-5 py-3 text-right font-medium">
                Own row
              </th>
              <th scope="col" className="px-5 py-3 text-right font-medium">
                Inherited
              </th>
              <th scope="col" className="px-5 py-3 text-right font-medium">
                N/A
              </th>
              <th scope="col" className="px-5 py-3 text-right font-medium">
                <span className="sr-only">Open</span>
              </th>
            </tr>
          </thead>

          <tbody>
            {group.members.map((member) => (
              <tr key={member.bundle.sku} className="grid-row hairline-b last:border-b-0">
                <th scope="row" className="px-5 py-3.5 text-left font-medium">
                  <Link
                    href={`/review/${member.bundle.sku}`}
                    className="mono text-[var(--accent)] hover:underline"
                  >
                    {member.bundle.sku}
                  </Link>
                  {member.isReference ? (
                    /*
                      Marked, not promoted. The reference is special in how extraction happened —
                      its spec block is where the shared values were read — and not special in the
                      catalogue. It is one of the orderable products like any other.
                    */
                    <span className="mt-1 block text-meta font-normal text-[var(--fg-quiet)]">
                      extracted from
                    </span>
                  ) : null}
                </th>

                {axis.map((code) => (
                  <td key={code} className="px-5 py-3.5 align-top">
                    <VariantCell bundle={member.bundle} code={code} />
                  </td>
                ))}

                <td className="px-5 py-3.5 text-right align-top tabular-nums">
                  {member.fromOwnRow}
                </td>
                <td className="px-5 py-3.5 text-right align-top tabular-nums text-[var(--fg-tertiary)]">
                  {member.inherited}
                </td>
                <td className="px-5 py-3.5 text-right align-top tabular-nums">
                  {member.inapplicable > 0 ? (
                    <span className="text-[var(--warn)]">{member.inapplicable}</span>
                  ) : (
                    <span className="text-[var(--fg-quiet)]">0</span>
                  )}
                </td>
                <td className="px-5 py-3.5 text-right align-top">
                  <Link
                    href={`/review/${member.bundle.sku}`}
                    className="btn btn-bare"
                    aria-label={`Open ${member.bundle.sku}`}
                  >
                    <ArrowIcon />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div className="mt-6 grid gap-6 sm:grid-cols-3">
        <Panel className="p-7">
          <Overline>Read from its own row</Overline>
          <p className="mt-3 figure">{totals.fromOwnRow}</p>
          <p className="mt-3 text-meta text-[var(--fg-quiet)]">
            Cited to a specific table cell rather than to a located quote, which is strictly more
            precise: the quote <em>is</em> the cell text, so there is no model claim to verify.
          </p>
        </Panel>

        <Panel className="p-7">
          <Overline>Inherited from the series</Overline>
          <p className="mt-3 figure text-[var(--fg-tertiary)]">{totals.inherited}</p>
          <p className="mt-3 text-meta text-[var(--fg-quiet)]">
            Shared specifications keep their original citation rather than being relabelled as a
            derivation. The value really was printed on the page, and the citation still resolves to
            the line stating it.
          </p>
        </Panel>

        <Panel className="p-7">
          <Overline>Not applicable at this size</Overline>
          <p
            className={
              totals.inapplicable > 0
                ? "mt-3 figure text-[var(--warn)]"
                : "mt-3 figure text-[var(--fg-tertiary)]"
            }
          >
            {totals.inapplicable}
          </p>
          <p className="mt-3 text-meta text-[var(--fg-quiet)]">
            A figure the source states for one size only is withheld from the others as
            inapplicable, not recorded as missing. That distinction is what stops a reviewer chasing
            a supplier for a number that was never meant to exist for their part.
          </p>
        </Panel>
      </div>
    </section>
  );
}

/**
 * One variant's value for a per-variant attribute, with the cell it came from.
 *
 * The cell reference is rendered rather than tucked into a tooltip. It is the entire evidentiary
 * claim this table makes — that these rows are distinct records read from distinct cells — and a
 * claim a reader has to hover to discover is one they will not check.
 */
function VariantCell({ bundle, code }: { bundle: SkuBundle; code: string }) {
  const value = bundle.values.find((entry) => entry.attribute_code === code);
  if (!value) {
    // The table had this column but not for this row. A blank cell in an ordering table is
    // ordinary, and inventing a value for it would be the one unforgivable thing here.
    return <span className="text-[var(--fg-quiet)]">—</span>;
  }

  const ref = tableRef(value.evidence[0]?.table_ref ?? null);
  return (
    <>
      <span className="block">{value.value_display ?? value.value_raw ?? "—"}</span>
      {ref ? <span className="mono mt-1 block text-meta text-[var(--fg-quiet)]">{ref}</span> : null}
    </>
  );
}

/**
 * The attributes the ordering table supplied, which are exactly the ones that vary.
 *
 * Derived from `table_extraction` across the whole series rather than from a hardcoded field like
 * `nominal_size`. Which columns a table carries is a property of the schema binding, not of this
 * component, and a ball valve's axis is not a pipe fitting's.
 */
function axisCodes(members: VariantMember[]): string[] {
  const codes = new Set<string>();
  for (const member of members) {
    for (const value of member.bundle.values) {
      if (value.method === "table_extraction") codes.add(value.attribute_code);
    }
  }
  return [...codes].sort();
}

function sum(members: VariantMember[], pick: (member: VariantMember) => number): number {
  return members.reduce((total, member) => total + pick(member), 0);
}
