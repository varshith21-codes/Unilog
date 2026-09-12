/**
 * Every number the public landing page puts on screen.
 *
 * The rule for this file is the rule the product enforces on itself: nothing is asserted
 * without a source, and the source is a path in this repository rather than a marketing
 * estimate. Each block records that path in `source`. It is deliberately not rendered — a page
 * of superscript markers competed with the figures it was meant to qualify — but it stays in
 * code, because the next person to change a number needs to know which file to check it against.
 *
 * Three constraints, because each is easy to violate later:
 *
 * 1. **Recorded, not live.** The corpus aggregates come from bundles checked in under
 *    `data/console/`. They describe runs that already happened, and the page says so. An
 *    animated counter implying live throughput would be the same category of dishonesty that
 *    `lib/stages.ts` goes out of its way to avoid.
 *
 * 2. **No rounded measurements.** Where a figure is 0.9505 it is written 0.9505, and where a
 *    decision score is 0.7283 it is not written 0.73. Rounding a measurement toward a
 *    friendlier number is how a spec sheet becomes a brochure.
 *
 * 3. **The unflattering figures stay.** Offline coverage of 50.7%, one wrong cell in the
 *    supplied-document arm, an untrained calibrator on the recorded bundle, and 3,377 gaps
 *    against 2,156 values are all on the page. A proof page that shows only clean output is
 *    an advertisement.
 *
 * Verified against disk on write, which changed two things the previous version of this page
 * showed:
 *
 * - The pair "decision score 0.728 / policy gate 0.707" was not invented, as a first pass at
 *   this suggested. 0.7283 is `values[body_material].score` in the BA-100-075 bundle, and
 *   0.7068 is the acceptance threshold that `scripts/export_console_fixture.py` derives for the
 *   five-SKU console fixture. Neither turns up in a plain grep because one is rounded and the
 *   other lives in a generated file that is not checked in. The figures on this page use the
 *   bundle's own threshold, 0.6853, instead — not because 0.7068 is wrong, but because a page
 *   tracing one record should quote the gate that record was actually decided against.
 * - The hero's locator was `t1:r5:c1`, which belongs to no value on the record. `body_material`
 *   cites a prose line on page 1 with no table reference; `nominal_size` is the value that
 *   cites a table cell, at `t1:r3`. Both are shown, because the difference is the point.
 */

// ---------------------------------------------------------------- provenance

/**
 * A block of figures read from one artifact.
 *
 * `source` is the repository path the block's numbers came from. It is not rendered — the page
 * carried a footnote apparatus for a while and it competed with the figures it was qualifying —
 * but it stays on every block, because the next person to change a number here needs to know
 * which file to check it against.
 */
export interface Cited {
  source: string;
}

// ---------------------------------------------------------------- hero record

/**
 * The record the hero traces end to end.
 *
 * BA-100-075, not the BA-100-025 this page used to name. Only 075 has a bundle on disk; 025
 * appears in the README variant table and across `evals/` but has no artifact behind it. A
 * page inviting you to audit a record should name one you can open.
 */
export const RECORD = {
  sku: "BA-100-075",
  classCode: "PLB.VLV.BALL.2PC",
  className: "Two-Piece Ball Valve",
  manufacturer: "Milwaukee Valve",
  documentId: "ba100@f7500023",
  docType: "spec_sheet",
  sha256: "f7500023aad5",
  pages: 1,
  lines: 26,
  tables: 1,
  size: "1.1 kB",
  parser: "text",
  pipelineVersion: "axiom-0.1.0",
  signatureVerified: true,
  source: "data/console/BA-100-075.bundle.json",
} as const satisfies Cited & Record<string, unknown>;

/** The four beats of one record's compilation, as the hero figure renders them. */
export const TRACE = [
  {
    index: "01",
    label: "Input",
    value: "6 supplier fields",
    detail: "Part number, description, three brand columns, manufacturer",
  },
  {
    index: "02",
    label: "Retrieved",
    value: "spec_sheet · 1 page",
    detail: "Stored under sha256 f7500023aad5 · 26 lines · 1 table",
  },
  {
    index: "03",
    label: "Verified span",
    value: "Bronze C84400",
    detail: "body_material · quote matched at 1.000 · page 1",
    highlight: true,
  },
  {
    index: "04",
    label: "Decision",
    value: "Auto-accepted",
    detail: "Score 0.7283 against a calibrated threshold of 0.6853",
    tone: "pass" as const,
  },
] as const;

/** The record's measured outcome. Read off the bundle, not recomputed. */
export const RECORD_OUTCOME = {
  attributesRequested: 23,
  attributesRequired: 12,
  valuesExtracted: 15,
  gaps: 8,
  gapsRequired: 3,
  citationCoverage: "100%",
  rejectedUnverifiable: 0,
  escalations: 0,
  checksRun: 15,
  failures: 0,
  warnings: 1,
  skippedRules: 3,
  autoAccepted: 15,
  queuedForReview: 0,
  withEvidence: 15,
  inferred: 0,
  fillRate: "75%",
  verifiability: "100%",
  consistency: "91.67%",
  wallClock: "10.65s",
  source: "data/console/BA-100-075.bundle.json",
} as const satisfies Cited & Record<string, unknown>;

/** The signed Quality Index, with the weights that produced the composite. */
export const QUALITY_INDEX = {
  completeness: { value: "0.75", weight: "0.35" },
  verifiability: { value: "1.00", weight: "0.30" },
  consistency: { value: "1.00", weight: "0.25" },
  richness: { value: "0.00", weight: "0.10" },
  source: "data/console/BA-100-075.bundle.json",
} as const satisfies Cited & Record<string, unknown>;

/**
 * The calibrated acceptance policy the record was decided against.
 *
 * `threshold` is the number the calibration set produced for the requested error budget, not
 * a constant somebody chose. That distinction is the reason the block exists.
 *
 * `calibrator` is on the page for the opposite reason. The recorded bundle was scored by an
 * untrained heuristic, which is a real limitation of this artifact, and a page that quotes
 * the threshold while hiding what produced it would be doing exactly what the product
 * refuses to do.
 */
export const POLICY = {
  epsilon: "5%",
  confidenceLevel: "95%",
  threshold: "0.6853",
  decisionScore: "0.7283",
  coverage: "100%",
  accepted: 125,
  acceptedErrors: 0,
  observedErrorRate: "0.0%",
  errorUpperBound: "2.12%",
  calibrationSize: 125,
  achievable: true,
  calibrator: "untrained-heuristic",
  method: "One-sided Wilson upper bound",
  reason:
    "at threshold 0.685, 100.0% of values are publishable with an error rate of at most " +
    "2.1% at 95% confidence",
  source: "data/console/BA-100-075.bundle.json",
} as const satisfies Cited & Record<string, unknown>;

/** The verified evidence span behind the hero value, field by field. */
export const EVIDENCE_SPAN = [
  { field: "quote", value: "Body Material .................. Bronze C84400" },
  { field: "document_id", value: "ba100@f7500023" },
  { field: "document_sha256", value: "f7500023aad5093f9f77…" },
  { field: "page", value: "1" },
  { field: "table_ref", value: "null — cited from a prose line" },
  { field: "quote_verified", value: "true", tone: "pass" as const },
  { field: "match_score", value: "1.000", tone: "pass" as const },
  { field: "method", value: "document_extraction" },
] as const;

/**
 * Three more values from the same record, to show the locator forms differ.
 *
 * `nominal_size` is the one that earns the parser: it cites a cell in the ordering table, so
 * a neighbouring row cannot be read as this part's size.
 */
export const SIBLING_VALUES = [
  {
    attribute: "nominal_size",
    value: '3/4"',
    locator: "t1:r3",
    quote: 'BA-100-075  | 3/4"   | Lever  | 12',
    method: "table_extraction",
    score: "0.7868",
  },
  {
    attribute: "pressure_rating_wog",
    value: "600 psi",
    locator: "page 1",
    quote: "Pressure Rating ................ 600 PSI WOG @ 73 degF",
    method: "document_extraction",
    score: "0.7889",
  },
  {
    attribute: "end_connection",
    value: "NPT Threaded",
    locator: "page 1",
    quote: "End Connection ................. NPT threaded, female both ends",
    method: "document_extraction",
    score: "0.6976",
    warned: true,
  },
] as const;

// ---------------------------------------------------------------- recorded corpus

/**
 * Aggregate over every bundle checked into `data/console/`.
 *
 * The headline is not the volume, it is the last pair: 3,377 gaps recorded against 2,156
 * values. A system that reports more of what it could not establish than what it did is
 * either broken or honest, and 1,007 of 1,007 certificates verifying settles which.
 */
export const CORPUS = {
  records: "1,007",
  values: "2,156",
  publishable: "265",
  needingReview: "75",
  gaps: "3,377",
  gapsRequired: "3,269",
  certificatesVerified: "1,007 / 1,007",
  documents: "1,007",
  modelCalls: "29",
  escalations: "0",
  inputTokens: "324,414",
  outputTokens: "37,924",
  totalCost: "$0.043455",
  source: "data/console/*.bundle.json — aggregate over 1,007 recorded runs",
} as const satisfies Cited & Record<string, unknown>;

/**
 * The band directly under the hero. Six figures, each with its denominator.
 *
 * Sources, in order: the delivery contract YAML, the recorded bundle aggregate,
 * `evals/adversarial.json`, `evals/baseline.json`, `progress.py`'s STAGE_PLAN, and the bundle
 * aggregate again.
 */
export const HEADLINE_FIGURES = [
  {
    label: "Delivery contract",
    value: "252",
    unit: "columns",
    hint: "79 populated in ground truth, 173 blank by design",
  },
  {
    label: "Recorded runs",
    value: "1,007",
    unit: "records",
    hint: "Certificates verified, 1,007 of 1,007",
  },
  {
    label: "Fabricated values",
    value: "0",
    unit: "of 64 adversarial requests",
    hint: "Every one abstained rather than answered",
    tone: "pass" as const,
  },
  {
    label: "Citation coverage",
    value: "100%",
    unit: "of published values",
    hint: "312 comparisons across 15 products",
    tone: "pass" as const,
  },
  {
    label: "Stages without a model",
    value: "9 / 13",
    unit: "deterministic",
    hint: "A model reads and classifies. It never decides publication",
  },
  {
    label: "Recorded gaps",
    value: "3,377",
    unit: "against 2,156 values",
    hint: "What could not be established is a record, not a silence",
  },
] as const;

// ---------------------------------------------------------------- pipeline

/**
 * The execution plan, in canonical order.
 *
 * `model` is the load-bearing column. The architectural claim is not that a model is
 * involved, it is where: reading and classifying, never deciding. Nine of thirteen stages
 * never call one, and every stage that can withhold a value is among those nine.
 */
export const PIPELINE = {
  total: 13,
  modelBacked: 4,
  deterministic: 9,
  source: "packages/axiom/pipeline/progress.py — STAGE_PLAN",
  stages: [
    {
      id: "retrieve",
      name: "Find the document",
      model: false,
      detail: "The stored library first, then the manufacturer's own site. Marketplaces are refused.",
    },
    {
      id: "ingest",
      name: "Ingest",
      model: false,
      detail:
        "Hashed and stored under its own SHA-256, so a citation written today still resolves " +
        "to the same bytes after the supplier silently replaces the file at that URL.",
    },
    {
      id: "parse",
      name: "Parse",
      model: false,
      detail: "Lines and addressable table cells, so a value can cite t1:r3 rather than a page number.",
    },
    {
      id: "classify",
      name: "Classify",
      model: true,
      detail: "The class decides which attributes are even asked for. ETIM and UNSPSC derive from it.",
    },
    {
      id: "extract",
      name: "Extract",
      model: true,
      detail: "Every value carries a verbatim quote. A quote that cannot be found is discarded, not weakened.",
    },
    {
      id: "merge",
      name: "Read the other sources",
      model: true,
      detail: "One citation proves a value was printed. Two prove it was not a typo.",
    },
    {
      id: "normalize",
      name: "Normalize",
      model: false,
      detail: "Canonical units, which is what makes two suppliers' figures comparable at all.",
    },
    {
      id: "validate",
      name: "Validate",
      model: false,
      detail: "Layers L0 to L3, deterministic. A rule that could not run counts as skipped, never as passed.",
    },
    {
      id: "decide",
      name: "Score and decide",
      model: false,
      detail: "Scored against the calibrated threshold. Below it becomes a review task, not a publication.",
    },
    {
      id: "export",
      name: "Syndicate",
      model: false,
      detail: "A channel missing a field it requires is held, rather than published with a gap.",
    },
    {
      id: "copy",
      name: "Generate copy",
      model: true,
      detail: "Claim-checked against extracted facts. A fluent sentence with an unsupported number is discarded.",
    },
    {
      id: "certify",
      name: "Certify",
      model: false,
      detail: "The Quality Index is signed. The certificate is auditable without trusting this console.",
    },
    {
      id: "persist",
      name: "File the result",
      model: false,
      detail: "Prompt version, model ID, schema version and document hash, so the run replays.",
    },
  ],
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- validation

/**
 * The seven validation layers.
 *
 * `when` is the column that matters and the one usually missing from a competitor's diagram.
 * L4 needs a second independent source and L6 needs a deployed reasoning policy; absent the
 * precondition, both report SKIPPED. Reporting them as PASS would convert "not checked" into
 * "checked and fine", which is the most expensive lie a validator can tell.
 */
export const VALIDATION_LAYERS = {
  source: "apps/console/src/lib/format.ts, apps/console/src/lib/types.ts",
  layers: [
    {
      id: "L0",
      name: "Type and format",
      when: "Every record",
      detail: "Enum resolution, GTIN check digits, qualifiers retained from the source.",
      example: "gtin_check_digit",
    },
    {
      id: "L1",
      name: "Dimensional",
      when: "Every record",
      detail: "Unit coherence, checked before any arithmetic is allowed to run.",
      example: "unit_coherence",
    },
    {
      id: "L2",
      name: "Domain rule",
      when: "Every record",
      detail: "Named cross-field rules declared per class and executed deterministically.",
      example: "R_STEAM_BELOW_WOG",
    },
    {
      id: "L3",
      name: "Statistical",
      when: "Every record",
      detail: "Plausible range against the observed distribution for the class.",
      example: "plausible_range",
    },
    {
      id: "L4",
      name: "Cross-source",
      when: "Two or more independent sources",
      detail:
        "Corroborated, superseded, conflict, or single-source. One document cannot " +
        "corroborate itself, and that is not a pass.",
      example: "L4_SINGLE_SOURCE",
      conditional: true,
    },
    {
      id: "L5",
      name: "Groundedness",
      when: "Enforced upstream",
      detail:
        "No validator by design: the extractor discards an unverifiable quote rather than " +
        "letting it reach a layer that could wave it through.",
      example: "quote_verified",
      conditional: true,
    },
    {
      id: "L6",
      name: "Formal",
      when: "Deployed reasoning policy",
      detail: "Solver-checked claims. Indeterminate is not a synonym for false.",
      example: "AR_NO_VERDICT",
      conditional: true,
    },
  ],
} as const satisfies Cited & Record<string, unknown>;

/** Named cross-field rules. The names are the argument, so they are quoted directly. */
export const RULES = {
  total: 67,
  classes: 32,
  source: "schema/classes/*.yaml — cross_field_rules",
  samples: [
    {
      id: "R_STEAM_BELOW_WOG",
      says: "A steam rating above the WOG rating means the WOG figure landed in the wrong field.",
    },
    {
      id: "R_LEADFREE_MATERIAL",
      says: "A lead-free claim is inconsistent with a leaded copper alloy body — C36000, C84400.",
    },
    {
      id: "R_PACK_WEIGHT",
      says: "Carton weight must match each-weight times carton quantity. The arithmetic proves one of the three is wrong.",
    },
    {
      id: "R_POTABLE_REQUIRES_NSF61",
      says: "A potable-water claim requires an NSF/ANSI 61 reference, cited.",
    },
    {
      id: "R_FULLPORT_CV_FLOOR",
      says: "A full-port valve with a low Cv usually means the figure was read from the reduced-port row.",
    },
    {
      id: "R_EXTINGUISHER_IS_NOT_A_DETECTOR",
      says: "Two different products, one product page.",
    },
  ],
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- provenance

/**
 * The five provenance classes of the delivery contract.
 *
 * This is what keeps a 79-column delivery expectation compatible with an evidence-or-null
 * rule instead of in conflict with it. Not every cell makes the same kind of claim, so not
 * every cell faces the same gate — and the cell that can cost a customer money faces the
 * full one.
 */
export const PROVENANCE_CLASSES = {
  source: "schema/delivery/unilog_delivery_v1.yaml",
  classes: [
    {
      id: "passthrough",
      claim: "You sent us this",
      gate: "No evidence needed",
      detail:
        "The client's six input columns, returned verbatim. Asserting it asserts only that " +
        "the row arrived, which is trivially true.",
    },
    {
      id: "derived",
      claim: "A deterministic function of established data",
      gate: "Inherits its input's provenance",
      detail:
        "Unit conversions, the merchandising hierarchy, UNSPSC from the class mapping. Code, " +
        "not model output.",
    },
    {
      id: "extracted",
      claim: "Read off a manufacturer source",
      gate: "Full gate — evidence span required",
      detail:
        "Validation and confidence policy enforced. These are the cells that can be wrong in " +
        "a way that costs a customer money, and a cell that fails the gate is emitted empty.",
      emphasis: true,
    },
    {
      id: "generated",
      claim: "Composed from established cells only",
      gate: "Claim-checked",
      detail:
        "May introduce wording. May not introduce facts. One unsupported claim fails the " +
        "block rather than scoring it down.",
    },
    {
      id: "unavailable",
      claim: "Cannot be established",
      gate: "Always emitted empty",
      detail:
        "Distributor-internal keys and list price. A fabricated value here would collide with " +
        "the client's own key space, which is the most damaging error in the format.",
    },
  ],
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- contract

/**
 * The 252-column delivery envelope.
 *
 * An envelope rather than a target, and the distinction is scored. Ground truth leaves 173
 * columns blank, and the grader checks emptiness in both directions — so filling a column the
 * client left empty costs points. `overfilled 0` is the invariant CI protects.
 *
 * Group columns sum to exactly 252 across 16 sections. `client_keys` and `taxonomy` each
 * appear twice in the file because the client interleaved other columns between them, which
 * is why 16 sections carry 14 distinct labels.
 */
export const CONTRACT = {
  columns: 252,
  populatedInGroundTruth: 79,
  blankByDesign: 173,
  sections: 16,
  groups: 14,
  joinKey: "Mfg_Part_Num",
  version: "unilog_delivery v1",
  attributeGridSlots: 50,
  source: "schema/delivery/unilog_delivery_v1.yaml",
  groupList: [
    { name: "attribute_grid", columns: 150, provenance: "extracted", note: "50 label/value/UOM triplets, one per class attribute binding" },
    { name: "assets", columns: 25, provenance: "derived", note: "Filenames written only once the asset was fetched and hashed" },
    { name: "item_features", columns: 20, provenance: "generated", note: "Claim-checked feature bullets. Legitimately sparse" },
    { name: "dimensions", columns: 10, provenance: "extracted", note: "Paired magnitude and unit columns" },
    { name: "reference_urls", columns: 6, provenance: "evidence", note: "Only a manufacturer-tier URL may be written to MFR URL" },
    { name: "input_echo", columns: 6, provenance: "passthrough", note: "The six input columns, returned unchanged" },
    { name: "descriptions", columns: 6, provenance: "generated", note: "The same product written at five lengths, plus marketing prose" },
    { name: "prose_slots", columns: 6, provenance: "extracted", note: "Approvals are strict-evidence only, never inferred" },
    { name: "identity", columns: 5, provenance: "derived", note: "Resolved against the approved manufacturer and brand master" },
    { name: "commercial", columns: 5, provenance: "extracted", note: "List price is never emitted. Pricing is not the manufacturer's" },
    { name: "taxonomy", columns: 4, provenance: "derived", note: "Reporting hierarchy and browse path, which genuinely disagree" },
    { name: "identifiers", columns: 4, provenance: "extracted", note: "UNSPSC is the exception — derived from the class mapping" },
    { name: "flags", columns: 3, provenance: "extracted", note: "Actual Image reports retrieval rather than defaulting to Yes" },
    { name: "client_keys", columns: 2, provenance: "unavailable", note: "Cannot be derived from a six-column input. Emitted empty" },
  ],
} as const satisfies Cited & Record<string, unknown>;

/** Graded delivery output, before and after the source documents were supplied. */
export const DELIVERY_SCORE = {
  source: "evals/delivery_score.json, evals/delivery_score_supplied.json",
  cellsCompared: 504,
  rowsScored: 2,
  arms: [
    {
      arm: "Description only",
      detail: "Six input columns, no attached document",
      exact: "56 / 134",
      missed: 78,
      overfilled: 0,
      wrong: 0,
      fillDiscipline: "426 / 504",
    },
    {
      arm: "Documents supplied",
      detail: "The same two rows, with the manufacturer datasheets attached",
      exact: "107 / 134",
      exactPercent: "79.9%",
      missed: 26,
      overfilled: 0,
      wrong: 1,
      fillDiscipline: "478 / 504",
      emphasis: true,
    },
  ],
  invariant:
    "overfilled 0 in both arms. The gate never guessed a cell in order to improve its own score, " +
    "and one wrong cell in the supplied arm is reported rather than smoothed away.",
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- measurements

/** The extraction backtest: a golden set, a pinned prompt version, named models. */
export const BACKTEST = {
  goldenSet: "pvf_valves_v1",
  measuredAt: "2026-08-05",
  promptVersion: "extract.v2",
  products: 15,
  comparisons: 312,
  calibrationSamples: 227,
  correct: 222,
  wrongValue: 2,
  missed: 3,
  correctlyAbstained: 85,
  hallucinated: 0,
  source: "evals/baseline.json",
  figures: [
    { label: "Precision", value: "0.991", hint: "222 correct, 2 wrong values", tone: "pass" as const },
    { label: "Recall", value: "0.978", hint: "3 missed of 227 available", tone: "pass" as const },
    { label: "F1", value: "0.984", hint: "Harmonic mean of the two", tone: "pass" as const },
    { label: "Hallucination rate", value: "0.0", hint: "0 fabricated across 312 comparisons", tone: "pass" as const },
    { label: "Abstention correctness", value: "1.0", hint: "85 of 85 refusals were correct", tone: "pass" as const },
    { label: "Exact match", value: "0.9505", hint: "String-identical to ground truth", tone: "pass" as const },
  ],
} as const satisfies Cited & Record<string, unknown>;

/**
 * Layout-only extraction: no model, no credentials, no network.
 *
 * The second figure is the interesting one. Coverage of 50.7% is not a good number and is
 * reported anyway, because the only way to raise it here is to guess the other half, which is
 * the failure this system exists to prevent.
 */
export const OFFLINE_EXTRACTION = {
  source: "evals/extraction_score.json",
  documents: 3,
  documentNames: "ap77c · ba100 · gv200",
  agreed: 115,
  disagreed: 0,
  notExtracted: 112,
  absentRespected: 85,
  absentViolated: 0,
  fabrications: 0,
  quoteFailures: 0,
  precision: "115 / 115",
  coverage: "115 / 227",
  coveragePercent: "50.7%",
} as const satisfies Cited & Record<string, unknown>;

/** Classification over a thousand real item-master rows. */
export const CLASSIFICATION = {
  source: "evals/classification_score.json",
  rows: "1,000",
  classes: 32,
  classified: 980,
  coverage: "98%",
  fabricated: 0,
  byMethod: [
    { method: "Retrieval only", count: 922, detail: "No model call at all" },
    { method: "Model adjudicated", count: 58, detail: "Candidates existed; the model chose between them" },
    { method: "No viable candidate", count: 11, detail: "Abstained" },
    { method: "Ambiguous, no model", count: 9, detail: "Abstained" },
  ],
  topClasses: [
    { code: "LGT.LMP.GEN", count: 220 },
    { code: "BLD.DCK.BOARD", count: 166 },
    { code: "TOL.PWR.GEN", count: 97 },
    { code: "ABR.WHL.BONDED", count: 46 },
    { code: "BLD.RAIL.POST", count: 45 },
    { code: "TOL.HND.GEN", count: 38 },
    { code: "TOL.ACC.DRIVERBIT", count: 35 },
    { code: "ELC.DEV.WIRING", count: 27 },
  ],
} as const satisfies Cited & Record<string, unknown>;

/**
 * The adversarial suite: right document, wrong product.
 *
 * The only test on this page where a passing grade means producing nothing. A system that
 * reads a gate valve datasheet and confidently fills in a ball valve's dimensions scores well
 * on coverage and is worthless.
 */
export const ADVERSARIAL = {
  source: "evals/adversarial.json",
  requested: 64,
  abstained: 64,
  fabricated: 0,
  fabricatedWithVerifiedQuote: 0,
  cost: "$0.00",
  cases: [
    {
      sku: "BA-100-075",
      document: "gv200.txt",
      requested: 23,
      abstained: 23,
      note: "gv200 describes NIBCO bronze gate valves. This is a Milwaukee ball valve, and it appears nowhere in the document.",
    },
    {
      sku: "T-113-100",
      document: "ba100.txt",
      requested: 18,
      abstained: 18,
      note: "The mirror case. A ball valve datasheet offered for a gate valve.",
    },
    {
      sku: "BA-100-999",
      document: "ba100.txt",
      requested: 23,
      abstained: 23,
      note: "The series is described but there is no -999 size. Shared family specs are the tempting wrong answer.",
    },
  ],
} as const satisfies Cited & Record<string, unknown>;

/** Before and after on one record, with a control SKU that received no treatment. */
export const COHORT = {
  source: "evals/cohort.json",
  treatment: {
    sku: "BA-100-075",
    before: { valuesPresent: 6, publishable: 0, withEvidence: 0, completeness: "0%", verifiability: "0%", composite: "0.2778" },
    after: { valuesPresent: 15, publishable: 15, withEvidence: 15, completeness: "75%", verifiability: "100%", composite: "0.9028" },
    lift: "+0.625",
  },
  /**
   * The control arm, reported as the file reports it: drift rather than a level.
   *
   * `control_drift` is 0.0 on every measured dimension, which is the claim that matters — the
   * treatment group moved and the untreated group did not, so the lift is attributable.
   */
  control: { sku: "T-113-100", drift: "0.0", dimensions: "completeness · verifiability · consistency · composite" },
  fieldPresence: { before: "0.3333", after: "0.75" },
  checksRun: { before: 11, after: 12 },
  excluded:
    "16 item-master rows had no enriched counterpart and were excluded rather than counted as " +
    "zero-improvement treatments. A run that never happened is not a null result.",
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- sourcing

/**
 * What the retriever refuses to read.
 *
 * The strongest content on the page, because each reason names a failure mode that looks
 * exactly like success. A marketplace citation resolves, quotes accurately, and passes every
 * check — and can still be someone else's guess about a different variant.
 */
export const SOURCING = {
  source: "schema/sourcing.yaml",
  tiers: "manufacturer · unknown · excluded",
  maxCandidatesPerSku: 4,
  minSecondsBetweenRequests: "2.0s",
  respectsRobotsTxt: true,
  mfrUrlTiers: "manufacturer tier only",
  documentTypes: 12,
  /**
   * Excluded host categories, matched on registrable domain plus subdomains rather than by
   * substring — `amazon.com` must not match `notamazon.com`.
   */
  excludedCategories: [
    "marketplace",
    "mass_retail",
    "industrial_distributor",
    "aggregator",
    "social_and_ugc",
    "reference",
  ],
  refusals: [
    {
      id: "Laundered provenance",
      detail:
        "An evidence chain that terminates in someone else's guess, while looking exactly " +
        "like a citation that terminates in the manufacturer's engineering drawing.",
    },
    {
      id: "Variant bleed",
      figure: "39",
      figureLabel: "fabrications, every one with a citation that checks out",
      detail:
        "The neighbouring row read as this part's specification. Measured in the adversarial " +
        "harness, and undetectable by inspecting the citation.",
    },
    {
      id: "Circularity",
      detail:
        "Enriching from a distributor risks reading back a value this system published, then " +
        "scoring it as independent corroboration.",
    },
  ],
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- taxonomy

/** Schema coverage. A new category is a YAML file, not a code change. */
export const TAXONOMY = {
  source: "schema/attributes/*.yaml, schema/classes/*.yaml",
  classes: 32,
  attributes: 126,
  bindings: 565,
  meanPerClass: "17.7",
  dictionaryFiles: 11,
  rules: 67,
  classCodes: [
    "PLB.VLV.BALL.2PC",
    "PLB.VLV.GATE.BRZ",
    "APP.KIT.DISHWASHER.BUILTIN",
    "APP.KIT.COOKING",
    "APP.KIT.COUNTERTOP",
    "APP.KIT.REFRIGERATION",
    "APP.LND.GEN",
    "TOL.PWR.GEN",
    "TOL.PWR.BATTERY",
    "TOL.HND.GEN",
    "TOL.LAY.GEN",
    "TOL.STG.GEN",
    "TOL.ACC.DRIVERBIT",
    "TOL.ACC.SAWBLADE",
    "ABR.WHL.BONDED",
    "ABR.COATED.GEN",
    "BLD.DCK.BOARD",
    "BLD.RAIL.POST",
    "BLD.PNL.SHEATHING",
    "BLD.WDW.GEN",
    "BLD.ROOF.GEN",
    "BLD.TAPE.GEN",
    "BLD.MAS.MORTAR",
    "ELC.DEV.WIRING",
    "ELC.BOX.GEN",
    "ELC.WIRE.GEN",
    "LGT.LMP.GEN",
    "LGT.FAN.CEILING",
    "SAF.APP.GEN",
    "SAF.EYE.GEN",
    "SAF.DET.GEN",
    "FST.GEN",
  ],
} as const satisfies Cited & Record<string, unknown>;

/** The class the hero record belongs to, opened up. */
export const CLASS_DETAIL = {
  source: "schema/classes/ball_valve_2pc.yaml",
  code: "PLB.VLV.BALL.2PC",
  name: "Two-Piece Ball Valve",
  version: "v1",
  attributes: 23,
  required: 12,
  rules: 7,
  channelProfiles: 3,
  etim: "EC002714",
  unspsc: "40141607",
  reportingPath: "Plumbing › Valves › Ball Valves",
  browsePath: "Plumbing › Valves › Ball Valves › Two-Piece",
  identityTerms: "valve · valves · vlv",
  weightedAttributes: [
    { code: "nominal_size", weight: "3.0", requirement: "Required" },
    { code: "pressure_rating_wog", weight: "3.0", requirement: "Required" },
    { code: "body_material", weight: "3.0", requirement: "Required" },
    { code: "end_connection", weight: "3.0", requirement: "Required" },
    { code: "lead_free_compliant", weight: "3.0", requirement: "Required" },
    { code: "port_type", weight: "2.0", requirement: "Required" },
    { code: "seat_material", weight: "2.0", requirement: "Required" },
    { code: "temperature_range", weight: "2.0", requirement: "Required" },
    { code: "approvals", weight: "2.0", requirement: "Required" },
    { code: "country_of_origin", weight: "2.0", requirement: "Required" },
    { code: "cv_flow_coefficient", weight: "1.0", requirement: "Recommended" },
    { code: "operating_torque", weight: "1.0", requirement: "Optional" },
  ],
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- economics

/**
 * Unit economics, priced from the AWS price list rather than a rate card in a comment.
 *
 * `usd()` in `lib/format.ts` renders below a cent at six decimals for the same reason this
 * block exists: two decimals turns every one of these into "$0.00", which reads as free
 * rather than as cheap and throws away the only interesting property of the number.
 */
export const ECONOMICS = {
  source: "data/console/BA-100-075.bundle.json",
  perRecord: "$0.001864",
  calls: 3,
  tiers: "2 volume · 1 mid",
  inputTokens: "4,343",
  outputTokens: "1,991",
  latency: "13.3s",
  wallClock: "10.65s",
  priceSource: "AWS Price List Query API (AmazonBedrock)",
  region: "us-east-2",
  pricedModels: 5,
  priceAge: "1.9 days",
  stale: false,
  corpusTotal: "$0.043455",
  corpusRecords: "1,007",
  corpusCalls: "29",
  note:
    "Most of the recorded corpus answered from the description alone, offline, with no model " +
    "call at all — which is why 1,007 records cost under five cents in total. The per-record " +
    "figure is a full document run, which is the honest number to quote.",
} as const satisfies Cited & Record<string, unknown>;

// ---------------------------------------------------------------- principles

/** The five rules the implementation is held to, in the order the README states them. */
export const PRINCIPLES = [
  {
    index: "01",
    title: "Evidence or null",
    detail:
      "A specification value cannot be written without a verifiable evidence span. No evidence " +
      "produces a typed gap record, not a guess — enforced by a validator rather than by " +
      "convention, because discipline does not survive a deadline.",
  },
  {
    index: "02",
    title: "Extraction and generation are separate subsystems",
    detail:
      "Specifications are extracted and proven. Marketing copy is generated, and constrained " +
      "to reference only facts that were already verified.",
  },
  {
    index: "03",
    title: "Deterministic where determinism exists",
    detail:
      "Unit conversion, check digits, dimensional consistency and arithmetic are code, not " +
      "model output. Nine of thirteen pipeline stages never call a model.",
  },
  {
    index: "04",
    title: "Confidence is calibrated, not asserted",
    detail:
      "The acceptance threshold comes from a held-out calibration set at a stated error budget " +
      "and confidence level, using a one-sided Wilson upper bound rather than a point estimate.",
  },
  {
    index: "05",
    title: "Everything is versioned and replayable",
    detail:
      "Prompt version, model ID, schema version and source document hash travel with every " +
      "value, so a decision made today can be re-derived and disputed later.",
  },
] as const;

/** The comparison table, phrased as the README phrases it. */
export const CONTRASTS = [
  { instead: "“The model is usually right”", axiom: "A measured error bound on everything auto-published" },
  { instead: "Free-text enrichment", axiom: "Evidence-or-null, enforced in the type system" },
  { instead: "A confidence number the model made up", axiom: "Confidence estimated from independently checkable signals" },
  { instead: "“Human review recommended”", axiom: "A queue ordered by failure mode, with the evidence on screen" },
  { instead: "A best-effort feed", axiom: "A pre-flight gate that refuses to publish an unreviewed value" },
] as const;

/** In-page navigation. Order matches the section order in `page.tsx`. */
export const SECTIONS = [
  { id: "evidence", label: "Evidence" },
  { id: "pipeline", label: "Pipeline" },
  { id: "validation", label: "Validation" },
  { id: "contract", label: "Contract" },
  { id: "measured", label: "Measured" },
  { id: "sourcing", label: "Sourcing" },
  { id: "coverage", label: "Coverage" },
] as const;
