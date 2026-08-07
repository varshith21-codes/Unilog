import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { VariantSeriesPanel } from "@/components/variant-series-panel";
import { variantGroups } from "@/lib/data";
import { variantBundle, variantValue } from "@/test/factories";

afterEach(cleanup);

/**
 * The panel exists to answer one sceptical question — were five products generated, or was one
 * copied five times? — so the tests are about whether the answer survives rendering. A panel that
 * showed the right SKU count while flattening the provenance would pass a layout review and fail at
 * the only thing it is for.
 */
function group(...bundles: Parameters<typeof variantBundle>[0][]) {
  const groups = variantGroups(bundles.map((spec) => variantBundle(spec)));
  if (groups.length === 0) throw new Error("fixture produced no variant series");
  return groups[0]!;
}

const SERIES = () =>
  group(
    {
      sku: "BA-100-050",
      parentSku: null,
      values: [
        variantValue({ code: "nominal_size", display: "DN15", fromCell: "t1:r1:c1" }),
        variantValue({ code: "body_material", display: "Bronze" }),
      ],
    },
    {
      sku: "BA-100-075",
      parentSku: "BA-100-050",
      values: [
        variantValue({ code: "nominal_size", display: "DN20", fromCell: "t1:r2:c1" }),
        variantValue({ code: "body_material", display: "Bronze", inheritedBy: "BA-100-075" }),
      ],
      inapplicable: 1,
    },
  );

describe("what the table shows", () => {
  it("lists every product in the series", () => {
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.getByText("BA-100-050")).toBeTruthy();
    expect(screen.getByText("BA-100-075")).toBeTruthy();
  });

  it("gives each variant its own value for the attribute that varies", () => {
    // The load-bearing assertion. Identical cells down this column would mean the pipeline copied
    // one record rather than reading each row.
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.getByText("DN15")).toBeTruthy();
    expect(screen.getByText("DN20")).toBeTruthy();
  });

  it("cites the exact table cell each value was read from", () => {
    render(<VariantSeriesPanel group={SERIES()} />);

    // `tableRef` renders t1:r2:c1 as prose. Whichever wording it chooses, the row must differ.
    expect(screen.getByText(/row 1/i)).toBeTruthy();
    expect(screen.getByText(/row 2/i)).toBeTruthy();
  });

  it("derives its columns from the ordering table rather than a hardcoded field", () => {
    // A ball valve's axis is not a pipe fitting's. The columns are whatever bound to the table.
    render(
      <VariantSeriesPanel
        group={group(
          { sku: "F-1", parentSku: null, values: [] },
          {
            sku: "F-2",
            parentSku: "F-1",
            values: [variantValue({ code: "thread_form", display: "NPT", fromCell: "t2:r2:c3" })],
          },
        )}
      />,
    );

    expect(screen.getByText("Thread form")).toBeTruthy();
    expect(screen.queryByText("Nominal size")).toBeNull();
  });

  it("shows a dash where the table had no value for a row", () => {
    // A blank cell in an ordering table is ordinary. Inventing a value for it would be the one
    // unforgivable thing this component could do.
    render(
      <VariantSeriesPanel
        group={group(
          {
            sku: "BA-100-050",
            parentSku: null,
            values: [variantValue({ code: "cv_rating", display: "12", fromCell: "t1:r1:c4" })],
          },
          { sku: "BA-100-075", parentSku: "BA-100-050", values: [] },
        )}
      />,
    );

    expect(screen.getByText("—")).toBeTruthy();
  });
});

describe("the provenance breakdown", () => {
  it("separates values read from a variant's own row from inherited ones", () => {
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.getByText(/Read from its own row/i)).toBeTruthy();
    expect(screen.getByText(/Inherited from the series/i)).toBeTruthy();
  });

  it("names a size-scoped exclusion as inapplicable rather than missing", () => {
    // The subtle case, and the reason this panel is worth having. A torque figure printed once for
    // the half-inch valve must not be inherited by four larger valves, and the reviewer must not be
    // sent chasing a supplier for a number that was never meant to exist for their part.
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.getByText(/Not applicable at this size/i)).toBeTruthy();
    expect(screen.getByText(/never meant to exist/i)).toBeTruthy();
  });

  it("marks which record the series was extracted from", () => {
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.getByText("extracted from")).toBeTruthy();
  });
});

describe("a series whose reference was never persisted", () => {
  it("says the reference is absent instead of listing a partial family as complete", () => {
    render(
      <VariantSeriesPanel
        group={group({ sku: "BA-100-075", parentSku: "BA-100-050", values: [] })}
      />,
    );

    expect(screen.getByText(/Reference record absent/i)).toBeTruthy();
  });

  it("does not warn when the reference is present", () => {
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.queryByText(/Reference record absent/i)).toBeNull();
  });
});

describe("navigation", () => {
  it("links every variant to its own review workspace", () => {
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(screen.getByLabelText("Open BA-100-050").getAttribute("href")).toBe(
      "/review/BA-100-050",
    );
    expect(screen.getByLabelText("Open BA-100-075").getAttribute("href")).toBe(
      "/review/BA-100-075",
    );
  });

  it("names the table for a screen reader rather than leaving it unlabelled", () => {
    // Resolved through the accessible name, not by finding the caption text somewhere on the page —
    // a caption that rendered outside the table would satisfy the latter and help nobody.
    render(<VariantSeriesPanel group={SERIES()} />);

    expect(
      screen.getByRole("table", { name: /Products in the BA-100-050 series/i }),
    ).toBeTruthy();
  });
});
