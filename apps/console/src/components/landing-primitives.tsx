/**
 * Shapes the public landing page reuses.
 *
 * These exist for the same reason `primitives.tsx` exists in the workspace: the page has nine
 * content sections, every one of them opens the same way, and expressing that as repeated
 * markup guarantees the ninth drifts from the first.
 *
 * Every visual value resolves to a token in `globals.css`. Nothing here carries a raw hex,
 * px, or shadow.
 *
 * Provenance is tracked in `data/landing.ts` rather than rendered: each figure block there
 * records the repository path it was read from, so the claim stays checkable in code without
 * putting an apparatus of footnote markers on the page.
 */

import type { ReactNode } from "react";

/**
 * Standard section opener.
 *
 * `eyebrow` names the concern, `title` states the claim, `lede` is the one paragraph that has
 * to survive a reader who skips everything else. All three are required because a section
 * that cannot fill them does not have a reason to exist.
 */
export function SectionHead({
  id,
  eyebrow,
  title,
  lede,
  aside,
}: {
  id: string;
  eyebrow: string;
  title: string;
  lede: ReactNode;
  /** Optional figure that belongs to the heading rather than to the section body. */
  aside?: ReactNode;
}) {
  return (
    <div className="landing-head">
      <div className="landing-head-copy">
        <p className="landing-eyebrow">{eyebrow}</p>
        <h2 id={id} className="landing-h2">
          {title}
        </h2>
      </div>
      <div className="landing-head-support">
        <p className="landing-lede">{lede}</p>
        {aside}
      </div>
    </div>
  );
}

export interface FigureItem {
  label: string;
  value: string;
  unit?: string;
  hint?: ReactNode;
  tone?: "default" | "pass" | "warn" | "fail";
}

/**
 * Rule-bounded band of headline figures.
 *
 * A band with vertical rules between columns rather than a row of cards. These figures share a
 * subject and a reader compares them against each other, so boxing each one would put three
 * borders between numbers that belong in one field.
 *
 * `hint` carries the denominator, and it is not optional in spirit: a figure without its
 * denominator is a number rather than a measurement, and "0 fabricated" means nothing until
 * you know it was 0 of 64.
 */
export function FigureBand({
  items,
  columns = 3,
  label,
}: {
  items: readonly FigureItem[];
  columns?: 2 | 3 | 4;
  label: string;
}) {
  return (
    <dl className="landing-figure-band" data-columns={columns} aria-label={label}>
      {items.map((item) => (
        <div key={item.label} className="landing-figure">
          <dt>{item.label}</dt>
          <dd>
            <strong data-tone={item.tone ?? "default"}>{item.value}</strong>
            {item.unit ? <span className="landing-figure-unit">{item.unit}</span> : null}
            {item.hint ? <span className="landing-figure-hint">{item.hint}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Dense specification table.
 *
 * Modelled on a tech-spec sheet rather than on a marketing comparison grid: a real `<table>`
 * with a real header row, tabular figures, and a hairline per row. It is the correct element —
 * this is tabular data, and a stack of divs would strip the row and column relationships an
 * assistive technology needs to read it.
 */
export function SpecTable({
  head,
  rows,
  caption,
  numeric,
}: {
  head: readonly string[];
  rows: readonly {
    key: string;
    cells: readonly ReactNode[];
    emphasis?: boolean;
    tone?: "pass" | "warn" | "quiet";
  }[];
  /** Named for assistive technology. Visually hidden, because the section heading says it too. */
  caption: string;
  /** Indices of columns holding figures, so they align on the decimal. */
  numeric?: readonly number[];
}) {
  const isNumeric = (index: number) => numeric?.includes(index) ?? false;

  return (
    <div className="landing-table-scroll">
      <table className="landing-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {head.map((cell, index) => (
              <th key={cell} scope="col" data-numeric={isNumeric(index) || undefined}>
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} data-emphasis={row.emphasis || undefined} data-tone={row.tone}>
              {row.cells.map((cell, index) =>
                index === 0 ? (
                  <th key={index} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td key={index} data-numeric={isNumeric(index) || undefined}>
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * A quiet, rule-bounded note.
 *
 * Used for the caveat that belongs to a figure rather than to the section: what a number does
 * not prove, why an unflattering result was left in. The page needs a lot of these, and they
 * must not compete with the figures they qualify.
 */
export function Note({
  kind = "neutral",
  children,
}: {
  kind?: "neutral" | "pass" | "warn";
  children: ReactNode;
}) {
  return (
    <p className="landing-note" data-kind={kind}>
      {children}
    </p>
  );
}
