/**
 * The content sections of the public landing page.
 *
 * Split out of `page.tsx` so the page itself reads as an outline. Each export here is one
 * section, owns one idea, and states one claim that the figures underneath it support.
 *
 * The composition rule the page follows: no two adjacent sections share a shape. A centred
 * heading over a wide console, then a two-column ladder, then a split table, then a mosaic,
 * then a full-bleed band. Six identical three-column card rows is the most common tell of a
 * generated page, and it is the thing this layout works hardest to avoid.
 */

import Link from "next/link";

import {
  ADVERSARIAL,
  BACKTEST,
  CLASSIFICATION,
  CLASS_DETAIL,
  COHORT,
  CONTRACT,
  CONTRASTS,
  CORPUS,
  DELIVERY_SCORE,
  ECONOMICS,
  EVIDENCE_SPAN,
  OFFLINE_EXTRACTION,
  PIPELINE,
  POLICY,
  PRINCIPLES,
  PROVENANCE_CLASSES,
  QUALITY_INDEX,
  RECORD,
  RECORD_OUTCOME,
  RULES,
  SIBLING_VALUES,
  SOURCING,
  TAXONOMY,
  VALIDATION_LAYERS,
} from "@/data/landing";
import { FigureBand, Note, SectionHead, SpecTable } from "@/components/landing-primitives";
import { ArrowIcon, CheckIcon } from "@/components/primitives";

// ---------------------------------------------------------------- 1. evidence

/**
 * The centrepiece: one value, and every decision attached to it.
 *
 * Structured as a governance column beside the source it rests on, because the whole argument
 * is the relationship between those two panes. The document facsimile is not decoration — it
 * is what a reviewer compares the extracted string against, character by character, which is
 * why the quote is set in monospace on both sides.
 */
export function EvidenceSection() {
  return (
    <section id="evidence" className="landing-section" aria-labelledby="evidence-title">
      <div className="marketing-container">
        <SectionHead
          id="evidence-title"
          eyebrow="The gate"
          title="One value. Every decision attached."
          lede={
            <>
              Follow <span className="mono-inline">body_material</span> on {RECORD.sku} from the
              line it was read off, through normalisation, validation and the calibrated
              acceptance policy, to the cell it is allowed to occupy. Nothing on this page was
              recomputed for the page; it is read off the recorded run.
            </>
          }
        />

        <article className="evidence-console" aria-label="Field-level evidence record">
          <header className="evidence-console-header">
            <p className="evidence-record-id">
              <span>Record</span>
              {RECORD.sku}
              <span>Class</span>
              {RECORD.classCode}
              <span>Pipeline</span>
              {RECORD.pipelineVersion}
            </p>
            <p className="evidence-output-target">
              <span className="pill pill-pass">
                <CheckIcon />
                Certificate verified
              </span>
            </p>
          </header>

          <div className="evidence-console-grid">
            <div className="evidence-governance">
              <p className="evidence-attribute-code">Field / body_material</p>
              <p className="evidence-attribute-name">Body material</p>
              <p className="evidence-value">Bronze C84400</p>

              <div className="evidence-decision">
                <strong>Publishable · Auto-accepted</strong>
                <span>
                  Decision score {POLICY.decisionScore} against a calibrated threshold of{" "}
                  {POLICY.threshold}
                </span>
              </div>

              <dl className="evidence-metadata">
                <div>
                  <dt>Method</dt>
                  <dd>document_extraction</dd>
                </div>
                <div>
                  <dt>Evidence</dt>
                  <dd className="evidence-pass">Verified · match 1.000</dd>
                </div>
                <div>
                  <dt>Locator</dt>
                  <dd>page 1</dd>
                </div>
                <div>
                  <dt>Document</dt>
                  <dd>{RECORD.documentId}</dd>
                </div>
              </dl>

              <div className="evidence-span-table">
                <p className="landing-overline">Evidence span, as stored</p>
                <dl>
                  {EVIDENCE_SPAN.map((field) => (
                    <div key={field.field}>
                      <dt>{field.field}</dt>
                      <dd data-tone={"tone" in field ? field.tone : undefined}>{field.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </div>

            <div className="evidence-source">
              <div className="evidence-document">
                <header className="evidence-document-header">
                  <span>
                    {RECORD.manufacturer} · {RECORD.docType}
                  </span>
                  <span>
                    sha256 {RECORD.sha256} · {RECORD.lines} lines · {RECORD.tables} table
                  </span>
                </header>
                <div className="evidence-document-page">
                  <p className="evidence-document-kicker">{RECORD.manufacturer}</p>
                  <h3>Two-Piece Full Port Bronze Ball Valve</h3>
                  <p className="evidence-document-line">
                    <span>Series</span>
                    <span>BA-100</span>
                  </p>
                  <p className="evidence-document-line evidence-document-line-highlight">
                    <span>Body Material</span>
                    <span>Bronze C84400</span>
                  </p>
                  <p className="evidence-document-line">
                    <span>Pressure Rating</span>
                    <span>600 PSI WOG @ 73 degF</span>
                  </p>
                  <p className="evidence-document-line">
                    <span>End Connection</span>
                    <span>NPT threaded, female both ends</span>
                  </p>
                  <p className="evidence-document-line">
                    <span>Seat Material</span>
                    <span>Reinforced PTFE</span>
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/*
            Three more values from the same record, because the locator forms differ and the
            difference matters. `nominal_size` is the one that earns the table parser: it cites a
            cell, so a neighbouring row of the ordering table cannot be read as this part's size.
          */}
          <div className="evidence-siblings">
            <SpecTable
              caption="Three further values from the same record, with their locators and scores"
              head={["Attribute", "Value", "Locator", "Method", "Score"]}
              numeric={[4]}
              rows={SIBLING_VALUES.map((value) => ({
                key: value.attribute,
                tone: "warned" in value && value.warned ? ("warn" as const) : undefined,
                cells: [
                  <span key="a" className="mono-inline">
                    {value.attribute}
                  </span>,
                  value.value,
                  <span key="l" className="mono-inline">
                    {value.locator}
                  </span>,
                  <span key="m" className="landing-muted">
                    {value.method}
                  </span>,
                  value.score,
                ],
              }))}
            />
          </div>

        </article>

        <FigureBand
          label="Outcome of the recorded run"
          columns={4}
          items={[
            {
              label: "Attributes populated",
              value: `${RECORD_OUTCOME.valuesExtracted} / ${RECORD_OUTCOME.attributesRequested}`,
              hint: `${RECORD_OUTCOME.attributesRequired} of them required by the class`,
            },
            {
              label: "Carrying evidence",
              value: `${RECORD_OUTCOME.withEvidence} / ${RECORD_OUTCOME.valuesExtracted}`,
              tone: "pass",
              hint: `${RECORD_OUTCOME.inferred} inferred · ${RECORD_OUTCOME.rejectedUnverifiable} rejected as unverifiable`,
            },
            {
              label: "Gaps recorded",
              value: String(RECORD_OUTCOME.gaps),
              hint: `${RECORD_OUTCOME.gapsRequired} of them on required attributes`,
            },
            {
              label: "Checks run",
              value: String(RECORD_OUTCOME.checksRun),
              hint: `${RECORD_OUTCOME.failures} failed · ${RECORD_OUTCOME.warnings} warned · ${RECORD_OUTCOME.skippedRules} skipped`,
            },
          ]}
        />

        <div className="landing-split">
          <div className="landing-split-main">
            <p className="landing-overline">Acceptance policy</p>
            <p className="landing-quote">{POLICY.reason}</p>
            <dl className="landing-inline-spec">
              <div>
                <dt>Error budget</dt>
                <dd>{POLICY.epsilon}</dd>
              </div>
              <div>
                <dt>Confidence level</dt>
                <dd>{POLICY.confidenceLevel}</dd>
              </div>
              <div>
                <dt>Threshold</dt>
                <dd>{POLICY.threshold}</dd>
              </div>
              <div>
                <dt>Error upper bound</dt>
                <dd>{POLICY.errorUpperBound}</dd>
              </div>
              <div>
                <dt>Calibration set</dt>
                <dd>{POLICY.calibrationSize} values</dd>
              </div>
              <div>
                <dt>Observed errors</dt>
                <dd>{POLICY.acceptedErrors}</dd>
              </div>
            </dl>
            <Note>
              {POLICY.method}, not a point estimate. If 40 held-out values clear a threshold and
              one is wrong, the observed error rate is 2.5% — but at that sample size the true
              rate could easily be 8%, so the bound is what the gate uses.
            </Note>
            <Note kind="warn">
              What this figure is not: the calibrator behind this recorded bundle is{" "}
              <span className="mono-inline">{POLICY.calibrator}</span>. The threshold is real and
              so is the bound over {POLICY.calibrationSize} samples, but a trained calibrator
              would move it. Stating that is cheaper than being caught not stating it.
            </Note>
          </div>

          <div className="landing-split-side">
            <p className="landing-overline">Signed quality index</p>
            <dl className="landing-qi">
              {(
                [
                  ["Completeness", QUALITY_INDEX.completeness],
                  ["Verifiability", QUALITY_INDEX.verifiability],
                  ["Consistency", QUALITY_INDEX.consistency],
                  ["Richness", QUALITY_INDEX.richness],
                ] as const
              ).map(([label, dimension]) => (
                <div key={label}>
                  <dt>
                    {label}
                    <span>weight {dimension.weight}</span>
                  </dt>
                  <dd>{dimension.value}</dd>
                </div>
              ))}
            </dl>
            <Note>
              Richness is 0.00 and stays on screen. Suppressing a dimension that scored zero
              would make the composite unreadable.
            </Note>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 2. pipeline

/**
 * The thirteen stages, with the four that call a model marked.
 *
 * The ratio is the architectural claim, so it is stated as a figure before the list rather
 * than left for the reader to count. Every stage that can withhold a value is in the
 * deterministic nine.
 */
export function PipelineSection() {
  return (
    <section
      id="pipeline"
      className="landing-section landing-section-sunken"
      aria-labelledby="pipeline-title"
    >
      <div className="marketing-container">
        <SectionHead
          id="pipeline-title"
          eyebrow="Execution"
          title="A model reads. It never decides."
          lede={
            <>
              Thirteen stages in canonical order. Four call a model — classify, extract, merge
              sources, generate copy — and all four only read or propose. Every stage that can
              withhold a value from publication is deterministic code.
            </>
          }
          aside={
            <div className="landing-ratio" aria-hidden>
              <span className="landing-ratio-value">
                {PIPELINE.deterministic}
                <i>/{PIPELINE.total}</i>
              </span>
              <span className="landing-ratio-label">stages without a model</span>
            </div>
          }
        />

        <ol className="landing-ladder" aria-label="Pipeline stages in execution order">
          {PIPELINE.stages.map((stage, index) => (
            <li key={stage.id} data-model={stage.model || undefined}>
              <span className="landing-ladder-index">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="landing-ladder-body">
                <p className="landing-ladder-name">
                  {stage.name}
                  <span className="landing-ladder-id">{stage.id}</span>
                  {stage.model ? (
                    <span className="pill pill-accent">Model</span>
                  ) : (
                    <span className="pill pill-quiet">Deterministic</span>
                  )}
                </p>
                <p className="landing-ladder-detail">{stage.detail}</p>
              </div>
            </li>
          ))}
        </ol>

        <Note>
          Four of the thirteen are dropped up front when a run cannot reach them, rather than
          marked skipped halfway through. A stage list that reports work it never attempted is
          the same defect as a validator that reports a check it never ran.
        </Note>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 3. validation

/**
 * Seven layers, and the column that says when each one actually runs.
 *
 * The `when` column is the point. L4 needs a second independent source and L6 needs a deployed
 * reasoning policy; without the precondition they report SKIPPED, because converting "not
 * checked" into "checked and fine" is the most expensive thing a validator can do.
 */
export function ValidationSection() {
  return (
    <section id="validation" className="landing-section" aria-labelledby="validation-title">
      <div className="marketing-container">
        <SectionHead
          id="validation-title"
          eyebrow="Validation"
          title="A check that did not run has established nothing."
          lede={
            <>
              Seven layers. Four run on every record. Three depend on a precondition, and when
              the precondition is absent they report skipped rather than pass — so a clean
              record and an unchecked one never look alike.
            </>
          }
        />

        <SpecTable
          caption="The seven validation layers, what triggers them and an example rule"
          head={["Layer", "Name", "Runs when", "Example rule", "What it establishes"]}
          rows={VALIDATION_LAYERS.layers.map((layer) => ({
            key: layer.id,
            tone: "conditional" in layer && layer.conditional ? ("quiet" as const) : undefined,
            cells: [
              <span key="id" className="landing-layer-id">
                {layer.id}
              </span>,
              layer.name,
              <span
                key="w"
                className={
                  "conditional" in layer && layer.conditional
                    ? "landing-conditional"
                    : undefined
                }
              >
                {layer.when}
              </span>,
              <span key="e" className="mono-inline">
                {layer.example}
              </span>,
              <span key="d" className="landing-muted">
                {layer.detail}
              </span>,
            ],
          }))}
        />

        <div className="landing-rule-grid">
          <div className="landing-rule-grid-head">
            <p className="landing-overline">Cross-field rules</p>
            <p className="landing-rule-count">
              {RULES.total}
              <span>declared across {RULES.classes} product classes</span>
            </p>
            <p className="landing-muted">
              Executed at L2. Each is arithmetic or set membership over already-normalised
              values, which is why a violation names the specific value that has to be wrong
              rather than lowering a score.
            </p>
          </div>
          <ul className="landing-rule-list">
            {RULES.samples.map((rule) => (
              <li key={rule.id}>
                <p className="mono-inline">{rule.id}</p>
                <p>{rule.says}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 4. contract

/**
 * The 252-column delivery envelope.
 *
 * Framed as an envelope rather than a completeness target, because that is how it is scored:
 * ground truth leaves 173 columns blank and the grader checks emptiness in both directions, so
 * filling a column the client left empty costs points. `overfilled 0` is the invariant.
 *
 * The mosaic is proportional rather than positional, and says so — 79 filled cells of 252, not
 * a claim about which 79.
 */
export function ContractSection() {
  return (
    <section id="contract" className="landing-section" aria-labelledby="contract-title">
      <div className="marketing-container">
        <SectionHead
          id="contract-title"
          eyebrow="Output contract"
          title="252 columns is an envelope, not a target."
          lede={
            <>
              The delivery format is the client's own, byte-identical down to the spaces in{" "}
              <span className="mono-inline">SKU - MY_PART_NUMBER</span>. Ground truth populates
              79 columns and leaves 173 empty, so filling a column they left blank is a defect
              rather than extra credit.
            </>
          }
        />

        <div className="landing-mosaic-block">
          <div
            className="landing-mosaic"
            role="img"
            aria-label={`${CONTRACT.populatedInGroundTruth} of ${CONTRACT.columns} columns are populated in ground truth; ${CONTRACT.blankByDesign} are empty by design`}
          >
            {Array.from({ length: CONTRACT.columns }, (_, index) => (
              <span
                key={index}
                data-filled={index < CONTRACT.populatedInGroundTruth || undefined}
              />
            ))}
          </div>
          <dl className="landing-mosaic-legend">
            <div>
              <dt>
                <i data-filled />
                Populated in ground truth
              </dt>
              <dd>{CONTRACT.populatedInGroundTruth}</dd>
            </div>
            <div>
              <dt>
                <i />
                Empty by design
              </dt>
              <dd>{CONTRACT.blankByDesign}</dd>
            </div>
            <div>
              <dt>Join key</dt>
              <dd className="mono-inline">{CONTRACT.joinKey}</dd>
            </div>
            <div>
              <dt>Sections / labels</dt>
              <dd>
                {CONTRACT.sections} / {CONTRACT.groups}
              </dd>
            </div>
          </dl>
          <Note>
            Proportional, not positional. The mosaic shows the ratio the contract declares, not
            which specific columns are filled for any one record.
          </Note>
        </div>

        <SpecTable
          caption="Column groups of the delivery contract, with their provenance class"
          head={["Group", "Columns", "Provenance", "Note"]}
          numeric={[1]}
          rows={CONTRACT.groupList.map((group) => ({
            key: group.name,
            emphasis: group.provenance === "extracted" && group.columns >= 150,
            cells: [
              <span key="n" className="mono-inline">
                {group.name}
              </span>,
              String(group.columns),
              <span key="p" className="landing-provenance" data-class={group.provenance}>
                {group.provenance}
              </span>,
              <span key="d" className="landing-muted">
                {group.note}
              </span>,
            ],
          }))}
        />

        <div className="landing-bento">
          <div className="landing-bento-head">
            <p className="landing-overline">Provenance classes</p>
            <h3 className="landing-h3">
              Not every cell makes the same kind of claim, so not every cell faces the same gate.
            </h3>
            <p className="landing-muted">
              This is what keeps a 79-column delivery expectation compatible with an
              evidence-or-null rule instead of in conflict with it. A CSV row carries no evidence
              span, so run naively the honest output is an empty file. The resolution is not to
              weaken the gate.
            </p>
          </div>
          {PROVENANCE_CLASSES.classes.map((provenance) => (
            <article
              key={provenance.id}
              className="landing-bento-cell"
              data-emphasis={"emphasis" in provenance && provenance.emphasis ? true : undefined}
            >
              <p className="landing-bento-id">{provenance.id}</p>
              <p className="landing-bento-claim">{provenance.claim}</p>
              <p className="landing-bento-gate">{provenance.gate}</p>
              <p className="landing-bento-detail">{provenance.detail}</p>
            </article>
          ))}
        </div>

        <div className="landing-split">
          <div className="landing-split-main">
            <p className="landing-overline">Graded against the client's own answer sheet</p>
            <SpecTable
              caption="Delivery grading, description-only against documents-supplied"
              head={["Arm", "Exact", "Missed", "Overfilled", "Wrong", "Fill discipline"]}
              numeric={[1, 2, 3, 4, 5]}
              rows={DELIVERY_SCORE.arms.map((arm) => ({
                key: arm.arm,
                emphasis: "emphasis" in arm && arm.emphasis ? true : undefined,
                cells: [
                  <span key="a">
                    {arm.arm}
                    <span className="landing-sub">{arm.detail}</span>
                  </span>,
                  arm.exact,
                  String(arm.missed),
                  String(arm.overfilled),
                  String(arm.wrong),
                  arm.fillDiscipline,
                ],
              }))}
            />
            <Note kind="pass">
              {DELIVERY_SCORE.invariant}
            </Note>
          </div>
          <div className="landing-split-side">
            <p className="landing-overline">Same record, before and after</p>
            <dl className="landing-delta">
              {(
                [
                  ["Values present", COHORT.treatment.before.valuesPresent, COHORT.treatment.after.valuesPresent],
                  ["Publishable", COHORT.treatment.before.publishable, COHORT.treatment.after.publishable],
                  ["With evidence", COHORT.treatment.before.withEvidence, COHORT.treatment.after.withEvidence],
                  ["Verifiability", COHORT.treatment.before.verifiability, COHORT.treatment.after.verifiability],
                  ["Composite", COHORT.treatment.before.composite, COHORT.treatment.after.composite],
                ] as const
              ).map(([label, before, after]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>
                    <span className="landing-delta-before">{before}</span>
                    <ArrowIcon />
                    <strong>{after}</strong>
                  </dd>
                </div>
              ))}
            </dl>
            <Note>
              Control SKU {COHORT.control.sku} drifted {COHORT.control.drift} on every measured
              dimension, which is what makes the lift attributable rather than incidental.
            </Note>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 5. measured

/** What was actually measured, including the results that do not flatter the system. */
export function MeasuredSection() {
  return (
    <section
      id="measured"
      className="landing-section landing-section-sunken"
      aria-labelledby="measured-title"
    >
      <div className="marketing-container">
        <SectionHead
          id="measured-title"
          eyebrow="Measured"
          title="Numbers from a backtest, not from a demo."
          lede={
            <>
              A pinned golden set, a pinned prompt version, named models, and a regression gate
              that refuses to lower a metric without an explicit override. Measured{" "}
              {BACKTEST.measuredAt} on {BACKTEST.goldenSet}.
            </>
          }
        />

        <FigureBand
          label="Extraction backtest"
          columns={3}
          items={BACKTEST.figures.map((figure) => ({ ...figure }))}
        />

        <div className="landing-triptych">
          <article>
            <p className="landing-overline">Offline extraction</p>
            <p className="landing-triptych-figure">{OFFLINE_EXTRACTION.precision}</p>
            <p className="landing-triptych-label">
              agreed, with {OFFLINE_EXTRACTION.disagreed} disagreements
            </p>
            <p className="landing-muted">
              Layout-only, on {OFFLINE_EXTRACTION.documents} real datasheets
              {" "}({OFFLINE_EXTRACTION.documentNames}). No model, no credentials, no network.
              {OFFLINE_EXTRACTION.absentRespected} absent values were correctly left absent, with{" "}
              {OFFLINE_EXTRACTION.absentViolated} violations.
            </p>
            <p className="landing-triptych-caveat">
              Coverage is {OFFLINE_EXTRACTION.coverage} — {OFFLINE_EXTRACTION.coveragePercent}. Not
              a good number, reported anyway: the only way to raise it here is to guess the other
              half.
            </p>
          </article>

          <article>
            <p className="landing-overline">Classification</p>
            <p className="landing-triptych-figure">{CLASSIFICATION.coverage}</p>
            <p className="landing-triptych-label">
              of {CLASSIFICATION.rows} real item-master rows
            </p>
            <p className="landing-muted">
              {CLASSIFICATION.classified} classified into {CLASSIFICATION.classes} classes,{" "}
              {CLASSIFICATION.fabricated} fabricated. The remaining 20 abstained rather than
              guessing a class, which is the correct outcome for a row that names a buying
              co-op instead of a manufacturer.
            </p>
            <dl className="landing-method-split">
              {CLASSIFICATION.byMethod.map((method) => (
                <div key={method.method}>
                  <dt>{method.method}</dt>
                  <dd>{method.count}</dd>
                </div>
              ))}
            </dl>
            <p className="landing-triptych-caveat">
              922 of 980 needed no model call at all.
            </p>
          </article>

          <article>
            <p className="landing-overline">Recorded corpus</p>
            <p className="landing-triptych-figure">{CORPUS.gaps}</p>
            <p className="landing-triptych-label">gaps against {CORPUS.values} values</p>
            <p className="landing-muted">
              Across {CORPUS.records} recorded runs. A system that files more of what it could
              not establish than what it did is either broken or honest, and{" "}
              {CORPUS.certificatesVerified} certificates verifying settles which.
            </p>
            <dl className="landing-method-split">
              <div>
                <dt>Publishable</dt>
                <dd>{CORPUS.publishable}</dd>
              </div>
              <div>
                <dt>Needing review</dt>
                <dd>{CORPUS.needingReview}</dd>
              </div>
              <div>
                <dt>Required gaps</dt>
                <dd>{CORPUS.gapsRequired}</dd>
              </div>
              <div>
                <dt>Escalations</dt>
                <dd>{CORPUS.escalations}</dd>
              </div>
            </dl>
            <p className="landing-triptych-caveat">
              Recorded, not live. These are runs that already happened, read off checked-in
              bundles.
            </p>
          </article>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 6. adversarial

/**
 * The full-bleed band, and the only test on the page where a pass means producing nothing.
 *
 * Given the right document for the wrong product, a system optimised for coverage fills in the
 * family specifications and scores well. This one returns nothing, three times out of three.
 */
export function AdversarialBand() {
  return (
    <section className="landing-band" aria-labelledby="adversarial-title">
      <div className="marketing-container">
        <div className="landing-band-inner">
          <div className="landing-band-copy">
            <p className="landing-eyebrow">Adversarial</p>
            <h2 id="adversarial-title" className="landing-band-title">
              Right document. Wrong product. Nothing published.
            </h2>
            <p className="landing-lede">
              A system tuned for coverage reads a gate valve datasheet, recognises the family
              specifications, and fills a ball valve's record with them — scoring well and being
              worthless. This is the one measurement where the passing grade is an empty result.
            </p>
          </div>

          <dl className="landing-band-figures" aria-label="Adversarial totals">
            <div>
              <dt>Requested</dt>
              <dd>{ADVERSARIAL.requested}</dd>
            </div>
            <div>
              <dt>Abstained</dt>
              <dd>{ADVERSARIAL.abstained}</dd>
            </div>
            <div>
              <dt>Fabricated</dt>
              <dd data-tone="pass">{ADVERSARIAL.fabricated}</dd>
            </div>
          </dl>
        </div>

        <ul className="landing-case-list" aria-label="Adversarial cases">
          {ADVERSARIAL.cases.map((testCase) => (
            <li key={`${testCase.sku}-${testCase.document}`}>
              <p className="landing-case-head">
                <span className="mono-inline">{testCase.sku}</span>
                <span aria-hidden>×</span>
                <span className="mono-inline">{testCase.document}</span>
              </p>
              <p className="landing-case-figure">
                {testCase.abstained} / {testCase.requested} abstained
              </p>
              <p className="landing-muted">{testCase.note}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 7. sourcing

/**
 * What the retriever refuses to read, and why each refusal is not obvious.
 *
 * The strongest content on the page: all three failure modes look exactly like success. A
 * marketplace citation resolves, quotes accurately, and passes every check downstream.
 */
export function SourcingSection() {
  return (
    <section id="sourcing" className="landing-section" aria-labelledby="sourcing-title">
      <div className="marketing-container">
        <SectionHead
          id="sourcing-title"
          eyebrow="Sourcing"
          title="A citation that checks out can still be wrong."
          lede={
            <>
              Only the manufacturer's own page may be written to the{" "}
              <span className="mono-inline">MFR URL</span> column. Marketplaces, mass retail and
              distributor catalogues are refused outright — not down-ranked, refused — and the
              three reasons are all failure modes that survive inspection.
            </>
          }
        />

        <div className="landing-refusals">
          {SOURCING.refusals.map((refusal) => (
            <article key={refusal.id}>
              <h3 className="landing-h3">{refusal.id}</h3>
              {"figure" in refusal && refusal.figure ? (
                <p className="landing-refusal-figure">
                  {refusal.figure}
                  <span>{refusal.figureLabel}</span>
                </p>
              ) : null}
              <p className="landing-muted">{refusal.detail}</p>
            </article>
          ))}
        </div>

        <div className="landing-split">
          <div className="landing-split-main">
            <p className="landing-overline">Excluded categories</p>
            <ul className="landing-tag-list">
              {SOURCING.excludedCategories.map((category) => (
                <li key={category}>{category}</li>
              ))}
            </ul>
            <Note>
              Matched on registrable domain plus subdomains, never by substring —{" "}
              <span className="mono-inline">amazon.com</span> must not match{" "}
              <span className="mono-inline">notamazon.com</span>. A third tier,{" "}
              <span className="mono-inline">unknown</span>, is fetchable but never promoted to
              the manufacturer URL column.
            </Note>
          </div>
          <div className="landing-split-side">
            <p className="landing-overline">Retrieval policy</p>
            <dl className="landing-inline-spec" data-stack>
              <div>
                <dt>Tiers</dt>
                <dd>{SOURCING.tiers}</dd>
              </div>
              <div>
                <dt>MFR URL eligibility</dt>
                <dd>{SOURCING.mfrUrlTiers}</dd>
              </div>
              <div>
                <dt>Candidates per SKU</dt>
                <dd>max {SOURCING.maxCandidatesPerSku}</dd>
              </div>
              <div>
                <dt>Request spacing</dt>
                <dd>{SOURCING.minSecondsBetweenRequests} minimum</dd>
              </div>
              <div>
                <dt>robots.txt</dt>
                <dd>respected</dd>
              </div>
              <div>
                <dt>Document types</dt>
                <dd>{SOURCING.documentTypes} recognised</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 8. coverage

/** Schema coverage, and the one class the hero record belongs to, opened up. */
export function CoverageSection() {
  return (
    <section
      id="coverage"
      className="landing-section landing-section-sunken"
      aria-labelledby="coverage-title"
    >
      <div className="marketing-container">
        <SectionHead
          id="coverage-title"
          eyebrow="Coverage"
          title="A new category is a YAML file, not a code change."
          lede={
            <>
              {TAXONOMY.classes} product classes bind {TAXONOMY.bindings} attributes drawn from a
              dictionary of {TAXONOMY.attributes}, averaging {TAXONOMY.meanPerClass} per class.
              The class decides which attributes are asked for, which rules run, and which
              channels can publish.
            </>
          }
        />

        <FigureBand
          label="Schema coverage"
          columns={4}
          items={[
            { label: "Product classes", value: String(TAXONOMY.classes), hint: "One YAML file each" },
            {
              label: "Attribute definitions",
              value: String(TAXONOMY.attributes),
              hint: `Across ${TAXONOMY.dictionaryFiles} dictionary files`,
            },
            {
              label: "Class bindings",
              value: String(TAXONOMY.bindings),
              hint: `Mean ${TAXONOMY.meanPerClass} per class`,
            },
            { label: "Cross-field rules", value: String(TAXONOMY.rules), hint: "Executed at L2" },
          ]}
        />

        <ul className="landing-tag-list landing-tag-list-mono" aria-label="Product class codes">
          {TAXONOMY.classCodes.map((code) => (
            <li key={code} data-active={code === CLASS_DETAIL.code || undefined}>
              {code}
            </li>
          ))}
        </ul>

        <div className="landing-split">
          <div className="landing-split-main">
            <p className="landing-overline">Class definition</p>
            <h3 className="landing-h3">
              {CLASS_DETAIL.code} <span className="landing-muted">· {CLASS_DETAIL.name}</span>
            </h3>
            <SpecTable
              caption="Weighted attributes of the two-piece ball valve class"
              head={["Attribute", "Requirement", "Weight"]}
              numeric={[2]}
              rows={CLASS_DETAIL.weightedAttributes.map((attribute) => ({
                key: attribute.code,
                emphasis: attribute.code === "body_material",
                cells: [
                  <span key="c" className="mono-inline">
                    {attribute.code}
                  </span>,
                  attribute.requirement,
                  attribute.weight,
                ],
              }))}
            />
            <Note>
              Weight is not importance in the abstract, it is what the completeness dimension of
              the Quality Index divides by. Twelve of the {CLASS_DETAIL.attributes} attributes
              are required.
            </Note>
          </div>
          <div className="landing-split-side">
            <p className="landing-overline">Mappings and paths</p>
            <dl className="landing-inline-spec" data-stack>
              <div>
                <dt>ETIM</dt>
                <dd className="mono-inline">{CLASS_DETAIL.etim}</dd>
              </div>
              <div>
                <dt>UNSPSC</dt>
                <dd className="mono-inline">{CLASS_DETAIL.unspsc}</dd>
              </div>
              <div>
                <dt>Reporting hierarchy</dt>
                <dd>{CLASS_DETAIL.reportingPath}</dd>
              </div>
              <div>
                <dt>Browse path</dt>
                <dd>{CLASS_DETAIL.browsePath}</dd>
              </div>
              <div>
                <dt>Identity terms</dt>
                <dd className="mono-inline">{CLASS_DETAIL.identityTerms}</dd>
              </div>
              <div>
                <dt>Channel profiles</dt>
                <dd>{CLASS_DETAIL.channelProfiles}</dd>
              </div>
            </dl>
            <Note>
              The reporting hierarchy and the browse path genuinely disagree in the client's own
              data, so both are declared. Deriving one from the other would be wrong.
            </Note>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 9. economics

/** Unit economics, priced from the AWS price list rather than from a rate card in a comment. */
export function EconomicsSection() {
  return (
    <section className="landing-section" aria-labelledby="economics-title">
      <div className="marketing-container">
        <SectionHead
          id="economics-title"
          eyebrow="Unit economics"
          title="Priced, not estimated."
          lede={
            <>
              Token usage is metered per call and costed against the live AWS price list, with
              the fetch age recorded so a stale table cannot quietly misstate the number. Cost
              renders below a cent at six decimals, because two would render every record as
              free.
            </>
          }
        />

        <FigureBand
          label="Cost of one full document run"
          columns={4}
          items={[
            { label: "Cost per record", value: ECONOMICS.perRecord, hint: `${ECONOMICS.calls} model calls · ${ECONOMICS.tiers}` },
            { label: "Tokens", value: ECONOMICS.inputTokens, unit: "in", hint: `${ECONOMICS.outputTokens} out` },
            { label: "Wall clock", value: ECONOMICS.wallClock, hint: `${ECONOMICS.latency} of model latency` },
            { label: "Recorded corpus", value: ECONOMICS.corpusTotal, hint: `${ECONOMICS.corpusRecords} records · ${ECONOMICS.corpusCalls} model calls` },
          ]}
        />

        <div className="landing-split">
          <div className="landing-split-main">
            <Note>{ECONOMICS.note}</Note>
          </div>
          <div className="landing-split-side">
            <dl className="landing-inline-spec" data-stack>
              <div>
                <dt>Price source</dt>
                <dd>{ECONOMICS.priceSource}</dd>
              </div>
              <div>
                <dt>Region</dt>
                <dd className="mono-inline">{ECONOMICS.region}</dd>
              </div>
              <div>
                <dt>Priced models</dt>
                <dd>{ECONOMICS.pricedModels}</dd>
              </div>
              <div>
                <dt>Table age</dt>
                <dd>
                  {ECONOMICS.priceAge} · not stale
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 10. principles

/** The five rules the implementation is held to, and what each one replaces. */
export function PrinciplesSection() {
  return (
    <section className="landing-section landing-section-sunken" aria-labelledby="principles-title">
      <div className="marketing-container">
        <SectionHead
          id="principles-title"
          eyebrow="Principles"
          title="Five rules, enforced by types rather than by intent."
          lede={
            <>
              Every one of these is checked somewhere that fails a build rather than
              somewhere that files a ticket. Discipline that depends on remembering does not
              survive a deadline.
            </>
          }
        />

        <ol className="landing-principles">
          {PRINCIPLES.map((principle) => (
            <li key={principle.index}>
              <span className="landing-principle-index">{principle.index}</span>
              <div>
                <h3 className="landing-h3">{principle.title}</h3>
                <p className="landing-muted">{principle.detail}</p>
              </div>
            </li>
          ))}
        </ol>

        <SpecTable
          caption="What the usual approach asserts, against what this one measures"
          head={["Instead of", "AXIOM does"]}
          rows={CONTRASTS.map((contrast) => ({
            key: contrast.instead,
            cells: [
              <span key="i" className="landing-muted">
                {contrast.instead}
              </span>,
              <strong key="a">{contrast.axiom}</strong>,
            ],
          }))}
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- closing

export function ClosingSection() {
  return (
    <section className="landing-closing" aria-labelledby="closing-title">
      <div className="marketing-container landing-closing-inner">
        <div>
          <p className="landing-eyebrow">Start</p>
          <h2 id="closing-title">Compile only what the evidence can support.</h2>
          <p>
            Enter the workspace to retrieve, verify, review and publish governed product
            records — or run one part number through the pipeline and read the trace it leaves.
          </p>
        </div>
        <div className="landing-actions">
          <Link href="/enrich" className="btn btn-primary">
            Enrich a product record
            <ArrowIcon />
          </Link>
          <Link href="/operations" className="btn btn-quiet">
            Open operations workspace
          </Link>
        </div>
      </div>
    </section>
  );
}

