/**
 * Products a tester can run without inventing a submission.
 *
 * The run screen needs two required fields and a manufacturer name that resolves, which means the
 * fastest way to get a bad first impression of this system is to type a part number it has never
 * heard of and watch it correctly report that it found nothing. These are real rows — every part
 * number and manufacturer string below is copied verbatim from the client's item master or from a
 * SKU already in `data/console/` — so a click produces a run that exercises a known code path.
 *
 * **Not fixtures, and not a whitelist.** Nothing here is special-cased anywhere in the pipeline. The
 * form accepts any part number; this list only saves typing. It lives in the console rather than
 * behind an API endpoint for exactly that reason: it is a convenience for whoever is driving the
 * screen, not a statement the service makes about itself.
 *
 * `expect` is the honest part. Each entry says what the run should produce *and why*, including the
 * ones that produce a deliberately thin result — a tester who only ever clicks the Mirka machines
 * would leave believing every part resolves to a manufacturer specification table, and most do not.
 * A sample set that only shows the system at its best is a demo, not a test.
 */

export interface EnrichSample {
  /** Group heading, so the list reads as cases rather than a pile of part numbers. */
  group: string;
  mpn: string;
  manufacturer: string;
  description: string;
  brand?: string;
  /** What this row demonstrates, and what a good result looks like. */
  expect: string;
  /** Set where the run is expected to be thin or to refuse, so a tester is not surprised by it. */
  tone?: "pass" | "warn";
}

export const ENRICH_SAMPLES: EnrichSample[] = [
  // ---------------------------------------------------------------- the strong case
  {
    group: "Manufacturer page found by a declared URL pattern",
    mpn: "8999000111",
    manufacturer: "Mirka",
    description: "Mirka® Dust Extractor 1025 L",
    brand: "Mirka",
    expect:
      "Reaches mirka.com/en/p/8999000111/ directly through the pattern declared for Mirka in " +
      "schema/sourcing.yaml, so it does not depend on what a search provider returns. Should read " +
      "all twelve rows of the manufacturer's specification table in English — cable length, dust " +
      "class, airflow, vacuum, power input.",
    tone: "pass",
  },
  {
    group: "Manufacturer page found by a declared URL pattern",
    mpn: "MIW9502022BA",
    manufacturer: "Mirka",
    description: "Mirka® LEROS 950CV EU 225 mm 5.0 mm orbit with bag",
    brand: "Mirka",
    expect:
      "The product page plus the datasheet PDFs linked from it, fetched in the same run. Shows the " +
      "linked-document arm: a page can cover the part while a linked technical sheet states far more.",
    tone: "pass",
  },
  {
    group: "Manufacturer page found by a declared URL pattern",
    mpn: "8896700140",
    manufacturer: "Mirka",
    description: "Abranet Max Flap Disc T29 125mm ALOX P40",
    brand: "Mirka",
    expect:
      "An abrasive rather than a machine, and one of the consumable codes mirka.com does route. " +
      "Classifies to ABR.COATED.GEN, so a different attribute set is requested.",
    tone: "pass",
  },

  // ---------------------------------------------------------------- the honest middle
  {
    group: "Manufacturer known, no declared pattern",
    mpn: "0887-20",
    manufacturer: "Milwaukee Accessory (4031)",
    description: "0887-20 Milw M18 Brushless Precision Blower",
    expect:
      "Milwaukee has a declared domain but no verified URL pattern, so retrieval searches their own " +
      "site first and the open web after. A good run finds a product page; a run where search " +
      "returns nothing falls back to the typed description, and the result says which happened.",
    tone: "pass",
  },
  {
    group: "Manufacturer known, no declared pattern",
    mpn: "1501831",
    manufacturer: "ProVia (PRODO)",
    description: "31.5x14.75 Bsmt ecoLitePlus WH - Hopper DLA w/Screen",
    brand: "PROVIA",
    expect:
      "Already has a manufacturer document stored in the library, so retrieval should answer from " +
      "disk with no network request at all. The cheapest possible run, and the one that shows why " +
      "the document library exists.",
    tone: "pass",
  },
  {
    group: "Manufacturer known, no declared pattern",
    mpn: "141465",
    manufacturer: "Phillips Lighting (5831)",
    description: '141465 15W Flor 18" T12 27k',
    brand: "Philips",
    expect:
      "A lighting part whose manufacturer name is misspelled in the item master (\u201cPhillips\u201d). " +
      "Resolution is fold-matched rather than exact, so it still reaches Signify/Philips.",
    tone: "pass",
  },

  // ---------------------------------------------------------------- the cases that should be thin
  {
    group: "Cases that should come back thin, and say so",
    mpn: "PDSH4816AF",
    manufacturer: "Appliance Dealers Cooperative (APPDE)",
    description: "PDSH4816AF Dishwasher SS - Display Only",
    expect:
      "The manufacturer field names a buying co-op, not a manufacturer. Expect it to be flagged and " +
      "kept out of MANUFACTURER_NAME rather than published — the screening in " +
      "axiom.delivery.source. A thin result here is the correct result.",
    tone: "warn",
  },
  {
    group: "Cases that should come back thin, and say so",
    mpn: "3MABR-7100075678",
    manufacturer: "Jam Industrial Supply LLC (JAMIN)",
    description: "3M 775L Stikit Film P150 - Cubitron II 50 Disc/Box",
    expect:
      "A distributor in the manufacturer column and a 3M part number prefixed by the distributor's " +
      "own code. Neither identifies a page on 3m.com, so expect retrieval to report why it found " +
      "nothing rather than reading a plausible-looking wrong document.",
    tone: "warn",
  },
  {
    group: "Cases that should come back thin, and say so",
    mpn: "52C3-5/8-UPC",
    manufacturer: "Southwire/g Turner (6603)",
    description: "4x4 1G Box Cover",
    brand: "Southwire",
    expect:
      "The separator case. A forward slash in a part number is a fractional size, not a path — this " +
      "run should persist under the slug 52C3-5~2F8-UPC while every artifact still carries the true " +
      "part number. Worth running once to prove the naming layer holds.",
    tone: "warn",
  },
];

/** The groups in display order, derived so a new sample cannot be added to an unrendered group. */
export function sampleGroups(): string[] {
  return [...new Set(ENRICH_SAMPLES.map((sample) => sample.group))];
}
