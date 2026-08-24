"use client";

/**
 * Enrich one product from typed fields, and show what the run produced.
 *
 * The one screen in this console that *causes* work rather than reading it. Everything else — the
 * dashboards, the replay, the audit list — projects output that a CLI run persisted earlier. This
 * posts a part number and spends two real Bedrock calls on it.
 *
 * That makes two things load-bearing that are cosmetic elsewhere.
 *
 * **The cost has to be visible before the button is pressed, not after.** A form that quietly bills
 * per submission is a form somebody will hold down. So the call count and the model tier are on the
 * submit control, and the measured cost is the first thing in the result.
 *
 * **The refusal has to name the field.** "A part number and a manufacturer are not enough" is true
 * and useless; "type a description or paste a datasheet link, because a part number identifies a
 * product but does not describe one" is what lets somebody finish. The submit stays disabled until
 * the submission can produce something, and the reason is on screen next to it rather than revealed
 * by pressing it.
 *
 * The outcome union mirrors `delivery-upload.tsx`. Its branches are the API's refusals one-for-one,
 * because collapsing two of them into a shared "failed" state would lose the remedy — `conflict`
 * offers a button, `unavailable` is an operator problem, and `busy` is worth retrying in ten seconds.
 */

import clsx from "clsx";
import Link from "next/link";
import { useId, useState } from "react";

import { ManufacturerSpecifications } from "@/components/manufacturer-specifications";
import { PipelineLive } from "@/components/pipeline-live";
import { AlertIcon, KeyValue, Overline, Panel } from "@/components/primitives";
import { runEnrichment } from "@/lib/actions";
// Types from `enrich`, which is erased at build time; the *function* from `enrich-request`, which is
// pure. Importing `isSubmittable` from `enrich` would pull `data.ts` and `node:fs/promises` into the
// client bundle and fail the build. See the docstring on `enrich-request.ts`.
import type { EnrichFailure, EnrichLimits, EnrichResponse } from "@/lib/enrich";
import { isSubmittable } from "@/lib/enrich-request";
import { count, dateTime, percent, shortHash, usd } from "@/lib/format";
import { certificateHref, reviewHref } from "@/lib/sku";
import { pipelineStages } from "@/lib/stages";

type Outcome =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "complete"; data: EnrichResponse }
  | { kind: "rejected"; failure: EnrichFailure };

interface Fields {
  mpn: string;
  manufacturer: string;
  description: string;
  sourceUrl: string;
  brand: string;
  includeOptional: boolean;
  generateCopy: boolean;
  retrieve: boolean;
  refreshSources: boolean;
}

const EMPTY: Fields = {
  mpn: "",
  manufacturer: "",
  description: "",
  sourceUrl: "",
  brand: "",
  includeOptional: false,
  generateCopy: false,
  // On, because it is what makes the two required fields sufficient.
  retrieve: true,
  refreshSources: false,
};

export function EnrichForm({ limits }: { limits: EnrichLimits }) {
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });

  const ids = useId();
  const mpnId = `${ids}-mpn`;
  const manufacturerId = `${ids}-manufacturer`;
  const descriptionId = `${ids}-description`;
  const urlId = `${ids}-url`;
  const brandId = `${ids}-brand`;
  const identityHeadingId = `${ids}-identity`;
  const sourceHeadingId = `${ids}-source`;
  const sourceGuidanceId = `${ids}-source-guidance`;
  const gateId = `${ids}-gate`;

  const running = outcome.kind === "running";
  const input = {
    mpn: fields.mpn,
    manufacturer: fields.manufacturer,
    description: fields.description,
    sourceUrl: fields.sourceUrl,
    brand: fields.brand,
    includeOptional: fields.includeOptional,
    generateCopy: fields.generateCopy,
    retrieve: fields.retrieve,
    refreshSources: fields.refreshSources,
  };
  const ready = isSubmittable(input);
  const calls = fields.generateCopy
    ? limits.model_calls_per_run.with_copy
    : limits.model_calls_per_run.without_copy;

  async function submit(replace = false) {
    if (!ready || running) return;
    setOutcome({ kind: "running" });
    const result = await runEnrichment({ ...input, replace });
    setOutcome(
      result.ok ? { kind: "complete", data: result.data } : { kind: "rejected", failure: result.failure },
    );
  }

  function set<K extends keyof Fields>(key: K, value: Fields[K]) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  function setRetrieval(value: boolean) {
    setFields((current) => ({
      ...current,
      retrieve: value,
      refreshSources: value ? current.refreshSources : false,
    }));
  }

  return (
    <>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(false);
        }}
        aria-busy={running}
        className="publish-form grid items-start gap-8 lg:grid-cols-12 lg:gap-10"
      >
        <div className="flex min-w-0 flex-col gap-10 lg:col-span-8">
          <section aria-labelledby={identityHeadingId} className="hairline-t pt-5">
            <StepHeading
              number="01"
              id={identityHeadingId}
              title="Identify the product"
              status="Required"
            />
            <p className="mt-3 max-w-prose text-sm text-[var(--fg-tertiary)]">
              The part number is the record&rsquo;s identity and the token extraction looks for in the
              source document. The manufacturer is recorded and echoed, and screened: a buying co-op
              in this field is flagged rather than written into{" "}
              <code className="mono text-[var(--fg-secondary)]">MANUFACTURER_NAME</code>.
            </p>

            <div className="mt-5 grid gap-5 sm:grid-cols-2">
              <Field
                id={mpnId}
                label="Manufacturer part number"
                value={fields.mpn}
                onChange={(value) => set("mpn", value)}
                disabled={running}
                placeholder="PDSH4816AF"
                required
                hint="Separators are fine. 52C3-5/8-UPC is a fractional size, not a problem."
              />
              <Field
                id={manufacturerId}
                label="Manufacturer"
                value={fields.manufacturer}
                onChange={(value) => set("manufacturer", value)}
                disabled={running}
                placeholder="Frigidaire"
                required
                hint="A trailing supplier code is stripped and kept: Kichler Lighting (KICLI)."
              />
            </div>
          </section>

          <section aria-labelledby={sourceHeadingId} className="hairline-t pt-5">
            <StepHeading
              number="02"
              id={sourceHeadingId}
              title="Add anything you already know"
              status="Optional"
            />
            <p id={sourceGuidanceId} className="mt-3 max-w-prose text-sm text-[var(--fg-tertiary)]">
              Both optional, because the two fields above are enough on their own — we look for the
              manufacturer&rsquo;s document ourselves. Fill these in when you already have them:
              nothing beats being told, and a URL you supply is used ahead of anything we would find.
              Supply a description too and it is read deterministically, with any document&rsquo;s
              values superseding it, because a datasheet <em>states</em> a fact where a description
              only implies it. The weaker reading stays in the record&rsquo;s history rather than
              being discarded.
            </p>

            <div className="mt-5 flex flex-col gap-5">
              <Field
                id={descriptionId}
                label="Product description"
                value={fields.description}
                onChange={(value) => set("description", value)}
                disabled={running}
                placeholder="24IN BUILT IN DISHWASHER STAINLESS STEEL 47DBA"
                textarea
                maxLength={limits.max_description_chars}
                hint={
                  `An ERP description is enough. Abbreviations are expanded from ` +
                  `schema/abbreviations.yaml, and every value cites the substring it came from. ` +
                  `Up to ${count(limits.max_description_chars)} characters.`
                }
              />
              <Field
                id={urlId}
                label="Manufacturer or datasheet URL"
                value={fields.sourceUrl}
                onChange={(value) => set("sourceUrl", value)}
                disabled={running}
                placeholder="https://example.com/spec/pdsh4816af.pdf"
                type="url"
                hint={
                  `${limits.url_schemes.join(", ")} only, up to ` +
                  `${(limits.max_document_bytes / (1024 * 1024)).toFixed(0)} MiB. One URL, one ` +
                  `document — nothing is crawled and no link is followed.`
                }
              />
              <Field
                id={brandId}
                label="Brand"
                value={fields.brand}
                onChange={(value) => set("brand", value)}
                disabled={running}
                placeholder="FRIGIDAIRE(R)"
                hint="Optional. Resolved against the approved brand master when it matches one."
              />
            </div>

            {/*
              The gate, stated rather than implied by a disabled button. Somebody who has filled in
              two fields and cannot press submit needs to know which third field is missing, and a
              greyed-out control does not say that.
            */}
            <p
              id={gateId}
              aria-live="polite"
              className={clsx(
                "mt-6 border-l-2 py-1 pl-4 text-sm",
                ready
                  ? "border-[var(--accent)] text-[var(--fg-secondary)]"
                  : "border-[var(--warn)] text-[var(--warn)]",
              )}
            >
              {gateMessage(fields, ready)}
            </p>
          </section>
        </div>

        <aside
          aria-labelledby={`${ids}-run-heading`}
          className="border border-[var(--hairline-strong)] bg-[var(--surface-raised)] shadow-sm lg:sticky lg:top-20 lg:col-span-4"
        >
          <header className="border-b border-[var(--hairline-strong)] p-5">
            <div className="flex items-center justify-between gap-4">
              <span className="mono text-[var(--accent)]">03</span>
              <span className="overline">Run control</span>
            </div>
            <h2 id={`${ids}-run-heading`} className="mt-3 text-lg font-medium">
              Run the pipeline
            </h2>
            <p className="mt-1 text-sm text-[var(--fg-tertiary)]">
              Online. Classification and extraction are real model calls.
            </p>
          </header>

          <fieldset
            disabled={running}
            className="border-b border-[var(--hairline)] p-5 disabled:opacity-60"
          >
            <legend className="overline">Scope</legend>
            <div className="mt-2 flex flex-col">
              <Toggle
                checked={fields.includeOptional}
                onChange={(value) => set("includeOptional", value)}
                label="Also request optional attributes"
                detail="A longer prompt and more tokens, for the attributes the class marks optional."
              />
              <Toggle
                checked={fields.generateCopy}
                onChange={(value) => set("generateCopy", value)}
                label="Generate and claim-check copy"
                detail="One extra model call. Copy that fails the check is reported, not published."
              />
              <Toggle
                checked={fields.retrieve}
                onChange={setRetrieval}
                label="Find the manufacturer's document"
                detail="The library first, then the manufacturer's own site. No model call, and it is what makes a part number and a manufacturer enough. Off requires a description or a URL."
              />
              <Toggle
                checked={fields.refreshSources}
                onChange={(value) => set("refreshSources", value)}
                disabled={!fields.retrieve}
                label="Refresh with richer live sources"
                detail="Bypasses cached SKU coverage once to look for a product page or linked technical datasheet, while retaining the stored document as fallback. No model call, but it may make network requests."
              />
            </div>
          </fieldset>

          {/*
            What this will cost, before it is spent. The one number on this page that has to be read
            before the button rather than after it.
          */}
          <div className="border-b border-[var(--hairline)] p-5">
            <Overline>What this run costs</Overline>
            <dl className="mt-3 flex flex-col gap-3">
              <KeyValue label="Model calls" mono>
                {calls}
              </KeyValue>
              <KeyValue label="Concurrency" mono>
                {limits.concurrent_runs} at a time
              </KeyValue>
            </dl>
            <p className="mt-3 text-meta text-[var(--fg-quiet)]">
              Billed to your AWS account per submission. The Publish page does the deterministic
              version of this for a whole file and costs nothing.
            </p>
          </div>

          <div className="p-5">
            <button
              type="submit"
              disabled={running || !ready}
              aria-describedby={gateId}
              data-loading={running ? "true" : "false"}
              className="btn btn-primary min-h-11 w-full"
            >
              {running ? "Running the pipeline…" : `Enrich this product (${calls} model calls)`}
            </button>
            <p
              role="status"
              aria-live="polite"
              aria-atomic="true"
              className="mt-3 text-center text-meta text-[var(--fg-tertiary)]"
            >
              {statusLine(outcome, ready)}
            </p>
            {outcome.kind !== "idle" && !running ? (
              <button
                type="button"
                onClick={() => {
                  setFields(EMPTY);
                  setOutcome({ kind: "idle" });
                }}
                className="btn btn-quiet mt-3 min-h-11 w-full"
              >
                Start another
              </button>
            ) : null}
          </div>
        </aside>
      </form>

      {outcome.kind === "rejected" ? (
        <Rejection failure={outcome.failure} onReplace={() => void submit(true)} />
      ) : null}
      {outcome.kind === "complete" ? <Result data={outcome.data} /> : null}
    </>
  );
}

// ------------------------------------------------------------------ copy

function gateMessage(fields: Fields, ready: boolean): string {
  if (!fields.mpn.trim()) return "A manufacturer part number is required.";
  if (!fields.manufacturer.trim()) return "A manufacturer is required.";
  if (!ready) {
    // Only reachable with retrieval off, which is the only state where those two fields are not
    // enough on their own.
    return (
      "Retrieval is off, so add a description or a manufacturer URL. With nowhere to look and " +
      "nothing to read, classification has nothing to classify, extraction has nothing to cite, " +
      "and the run would cost two model calls to return an identity-only row."
    );
  }
  if (fields.sourceUrl.trim()) {
    return fields.description.trim()
      ? "Ready. The datasheet is the source; the description is read too, and the document supersedes it."
      : "Ready. The fetched document is the source, and values cite the page and line they came from.";
  }
  if (!fields.retrieve) {
    return "Ready. Retrieval is off, so the submission itself is the source — hashed and citable, and a weaker claim than a datasheet.";
  }
  return fields.description.trim()
    ? "Ready. We will look for the manufacturer's own document, and read your description either way."
    : "Ready. We will look for the manufacturer's own document — the library first, then their own site. If nothing is found, your typed fields become the source.";
}

function statusLine(outcome: Outcome, ready: boolean): string {
  switch (outcome.kind) {
    case "running":
      return "Classifying and extracting. A model call takes a few seconds; an escalation to a larger model takes longer.";
    case "complete":
      return `Run complete. ${outcome.data.sku} is now in the Resolve queue and the Audit list.`;
    case "rejected":
      return "Nothing was run.";
    default:
      return ready ? "Ready to run." : "Fill in the fields above.";
  }
}

// ------------------------------------------------------------------ inputs

function StepHeading({
  number,
  id,
  title,
  status,
}: {
  number: string;
  id: string;
  title: string;
  status: string;
}) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
      <div className="flex min-w-0 items-baseline gap-3">
        <span className="mono shrink-0 text-[var(--accent)]">{number}</span>
        <h2 id={id} className="text-xl font-medium tracking-[var(--tracking-heading)]">
          {title}
        </h2>
      </div>
      <span className="overline">{status}</span>
    </header>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  hint,
  placeholder,
  disabled = false,
  required = false,
  textarea = false,
  type = "text",
  maxLength,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  textarea?: boolean;
  type?: string;
  maxLength?: number;
}) {
  const hintId = `${id}-hint`;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-[var(--fg)]">
        {label}
        {required ? null : (
          <span className="ml-2 text-meta font-normal text-[var(--fg-quiet)]">optional</span>
        )}
      </label>
      {textarea ? (
        <textarea
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          placeholder={placeholder}
          maxLength={maxLength}
          rows={3}
          aria-describedby={hintId}
          className="mono min-h-11 w-full"
        />
      ) : (
        <input
          id={id}
          type={type}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          placeholder={placeholder}
          maxLength={maxLength}
          aria-describedby={hintId}
          className="mono min-h-11 w-full"
        />
      )}
      <p id={hintId} className="text-meta text-[var(--fg-quiet)]">
        {hint}
      </p>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  detail,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  detail: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={clsx(
        "flex items-start gap-3 border-b border-[var(--hairline)] py-3 text-sm text-[var(--fg-secondary)] last:border-b-0",
        disabled
          ? "cursor-not-allowed opacity-60"
          : "cursor-pointer hover:text-[var(--fg)] active:opacity-80",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
        className="mt-1"
      />
      <span className="min-w-0">
        <span className="block">{label}</span>
        <span className="mt-1 block text-meta text-[var(--fg-quiet)]">{detail}</span>
      </span>
    </label>
  );
}

// ------------------------------------------------------------------ refusals

function Rejection({
  failure,
  onReplace,
}: {
  failure: EnrichFailure;
  onReplace: () => void;
}) {
  const headingId = "enrich-rejected-heading";
  const tone = failure.kind === "conflict" ? "warn" : "fail";

  return (
    <section
      aria-labelledby={headingId}
      className={clsx(
        "animate-rise mt-10 border-y py-6",
        tone === "warn" ? "border-[var(--warn)]" : "border-[var(--fail)]",
      )}
    >
      <div className="px-5 sm:px-6">
        <p
          className={clsx(
            "overline flex items-center gap-2",
            tone === "warn" ? "text-[var(--warn)]" : "text-[var(--fail)]",
          )}
        >
          <AlertIcon />
          {failure.kind === "conflict" ? "Already enriched · nothing run" : "Refused · nothing run"}
        </p>
        <h2 id={headingId} className="mt-2 text-xl font-medium tracking-[var(--tracking-heading)]">
          {rejectionTitle(failure)}
        </h2>
        <p className="mt-3 max-w-3xl text-sm text-[var(--fg-tertiary)]">{failure.message}</p>

        {failure.kind === "insufficient_input" && failure.missing.length > 0 ? (
          <ul className="mt-4 flex flex-wrap gap-3">
            {failure.missing.map((field) => (
              <li key={field}>
                <code className="mono border-l-2 border-[var(--fail)] pl-3 text-[var(--fail)]">
                  {field}
                </code>
              </li>
            ))}
          </ul>
        ) : null}

        {failure.kind === "conflict" ? (
          <div className="mt-5">
            {failure.enrichedAt ? (
              <p className="text-meta text-[var(--fg-quiet)]">
                Last run <time dateTime={failure.enrichedAt}>{dateTime(failure.enrichedAt)}</time>
              </p>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {/*
                Replace is offered, never assumed. Re-running discards the session a reviewer may
                already have worked and the decisions recorded against it, so it takes a second press
                that says so.
              */}
              <button type="button" onClick={onReplace} className="btn btn-primary min-h-11">
                Re-run and replace the saved session
              </button>
              {failure.slug ? (
                <Link href={`/review/${failure.slug}`} className="btn btn-quiet min-h-11">
                  Open the existing run instead
                </Link>
              ) : null}
            </div>
            <p className="mt-3 max-w-2xl text-meta text-[var(--fg-quiet)]">
              Replacing discards the existing review session and any decisions on it, and spends
              another set of model calls.
            </p>
          </div>
        ) : null}

        {failure.kind === "unavailable" ? (
          <div className="mt-5 border-l-2 border-[var(--hairline-strong)] pl-4">
            <Overline>What the API reported</Overline>
            <p className="mono mt-2 break-words text-sm text-[var(--fg-secondary)]">
              {failure.detail}
            </p>
            {failure.region ? (
              <p className="mt-2 text-meta text-[var(--fg-quiet)]">Region {failure.region}</p>
            ) : null}
          </div>
        ) : null}

        {failure.kind === "source_unreachable" && failure.url ? (
          <p className="mono mt-4 break-all text-sm text-[var(--fg-quiet)]">{failure.url}</p>
        ) : null}
      </div>
    </section>
  );
}

function rejectionTitle(failure: EnrichFailure): string {
  switch (failure.kind) {
    case "insufficient_input":
      return "There is nothing here to read";
    case "invalid":
      return "That submission was not accepted";
    case "conflict":
      return "This part number has already been enriched";
    case "busy":
      return "Another run is in flight";
    case "source_unreachable":
      return "The document could not be fetched";
    case "unavailable":
      return "The model is not available";
    case "unreachable":
      return "The AXIOM API is not reachable";
    default:
      return "The run failed";
  }
}

// ------------------------------------------------------------------ result

function Result({ data }: { data: EnrichResponse }) {
  const { summary, delivery, queue } = data;
  const quality = summary.certificate.quality_index;
  const stages = pipelineStages(data.bundle, data.document, data.policy, null);
  const bundledManufacturerSpecifications = data.bundle.manufacturer_specifications;
  const manufacturerSpecifications = bundledManufacturerSpecifications ?? [];
  const mappedManufacturerSpecifications = manufacturerSpecifications.filter(
    (specification) =>
      typeof specification.mapped_attribute_code === "string" &&
      specification.mapped_attribute_code.trim().length > 0,
  ).length;
  const derivedManufacturerSpecificationSummary = {
    total: manufacturerSpecifications.length,
    mapped: mappedManufacturerSpecifications,
    unmapped: manufacturerSpecifications.length - mappedManufacturerSpecifications,
  };
  const manufacturerSpecificationSummary =
    bundledManufacturerSpecifications !== undefined
      ? derivedManufacturerSpecificationSummary
      : (summary.manufacturer_specifications ?? derivedManufacturerSpecificationSummary);

  return (
    <div className="animate-rise mt-10 flex flex-col gap-[var(--spacing-section)]">
      <section aria-labelledby="enrich-result-heading">
        <p className="overline text-[var(--pass)]">
          Run complete · {data.replaced ? "saved run replaced" : "persisted"}
        </p>
        <h2
          id="enrich-result-heading"
          className="mt-2 text-xl font-medium tracking-[var(--tracking-heading)]"
        >
          {data.sku}
          <span className="mono ml-3 text-base text-[var(--fg-tertiary)]">
            {summary.class_code ?? "unclassified"}
          </span>
        </h2>
        <p className="mt-2 max-w-3xl text-sm text-[var(--fg-tertiary)]">
          {summary.class_from_fallback
            ? "Classification abstained, so the class you supplied was used. That is a different claim from having classified it."
            : "Classified, extracted, validated, scored and certified. Every figure below was measured by this run."}
        </p>

        <dl className="mt-6 grid grid-cols-2 gap-x-5 gap-y-7 border-y border-[var(--hairline-strong)] py-6 lg:grid-cols-5 lg:gap-x-6">
          <Metric
            label="Typed values"
            value={count(summary.values.total)}
            hint={`${summary.values.publishable} publishable, ${summary.values.needing_review} queued`}
          />
          <Metric
            label="Source specifications"
            value={count(manufacturerSpecificationSummary.total)}
            hint={`${manufacturerSpecificationSummary.mapped} mapped, ${manufacturerSpecificationSummary.unmapped} source-only`}
          />
          {/*
            No quality figure at all for an unclassified run, rather than the one the certificate
            carries. `fill_rate` over an empty required set returns 1.0 vacuously, so an identity-only
            record certifies at "100% complete" — and printing that next to zero values would be the
            most misleading number on the page. Same treatment the Resolve queue already gives an
            unclassified SKU: unmeasured classification is not zero completeness, and it is certainly
            not full completeness.
          */}
          {summary.class_code === null ? (
            <Metric
              label="Quality composite"
              value="unmeasured"
              hint="no class, so there is no denominator to score completeness against"
              tone="warn"
            />
          ) : (
            <Metric
              label="Quality composite"
              value={percent(quality.composite ?? 0)}
              hint={`completeness ${percent(quality.completeness)} · verifiability ${percent(quality.verifiability)}`}
            />
          )}
          <Metric
            label="Cost"
            value={summary.cost.usd === null ? "unpriced" : usd(summary.cost.usd)}
            hint={`${summary.cost.calls} model call${summary.cost.calls === 1 ? "" : "s"}`}
          />
          <Metric
            label="Certificate"
            value={summary.certificate.signature_verified ? "VERIFIED" : "UNVERIFIED"}
            hint={summary.certificate.certificate_id}
            tone={summary.certificate.signature_verified ? "pass" : "fail"}
          />
        </dl>
      </section>

      {/*
        The stages, framed as the execution they were. Same projection the replay screen renders,
        opposite claim about when it happened.
      */}
      <PipelineLive stages={stages} sku={data.sku} summary={summary} />

      <section aria-labelledby="enrich-provenance-heading">
        <h3 id="enrich-provenance-heading" className="overline">
          Where the values came from
        </h3>
        {/*
          How the document was found, before what was read from it. On this screen that is the first
          question — "did you actually find the manufacturer's page, or are you reading my own typed
          fields back to me?" — and it is the one a thin result is otherwise mistaken for.
        */}
        {summary.retrieval.attempted ? (
          <div className="mt-4 border-l-2 border-[var(--hairline-strong)] pl-4">
            <Overline>How the document was found</Overline>
            {summary.retrieval.found ? (
              <>
                <p className="mt-2 text-sm text-[var(--fg)]">
                  {summary.retrieval.from_library
                    ? "Reused a stored manufacturer document — no new network request was needed."
                    : `Retrieved from ${summary.retrieval.manufacturer?.domain ?? "the manufacturer"}.`}
                  {typeof summary.retrieval.requests_made === "number" ? (
                    <span className="text-[var(--fg-quiet)]">
                      {" "}
                      {summary.retrieval.requests_made} request
                      {summary.retrieval.requests_made === 1 ? "" : "s"} · no model call
                    </span>
                  ) : null}
                </p>
                {summary.retrieval.documents?.map((doc) => (
                  <p key={doc.sha256} className="mono mt-2 break-all text-meta text-[var(--fg-tertiary)]">
                    {doc.uri}
                  </p>
                ))}
              </>
            ) : (
              <p className="mt-2 max-w-3xl text-sm text-[var(--warn)]">
                No manufacturer document was found, so the typed fields are the source. The notes
                below say why, and what would change it.
              </p>
            )}
          </div>
        ) : null}

        <div className="mt-4 grid gap-6 border-y border-[var(--hairline-strong)] py-6 md:grid-cols-3">
          <div className="border-l-2 border-[var(--accent)] pl-4">
            <Overline>Source document</Overline>
            <p className="mono mt-2 break-words text-sm text-[var(--fg)]">
              {summary.source.document_id}
            </p>
            <p className="mono mt-1 text-meta text-[var(--fg-quiet)]">
              sha256 {shortHash(summary.source.sha256, 16)}…
            </p>
            <p className="mt-3 text-meta text-[var(--fg-tertiary)]">
              {summary.source.evidential_weight}
            </p>
          </div>
          <div className="border-l border-[var(--hairline-strong)] pl-4">
            <Overline>Read from the description</Overline>
            <p className="mono mt-2 text-[var(--fg)]">
              {summary.from_description.extracted} kept, {summary.from_description.refused} refused
            </p>
            <p className="mt-3 text-meta text-[var(--fg-tertiary)]">
              Deterministic and class-scoped. Each value cites the substring it was read from, and a
              document value supersedes it.
            </p>
          </div>
          <div className="border-l border-[var(--hairline-strong)] pl-4">
            <Overline>Manufacturer</Overline>
            <p className="mono mt-2 break-words text-[var(--fg)]">
              {summary.manufacturer.name ?? "—"}
            </p>
            <p
              className={clsx(
                "mt-3 text-meta",
                summary.manufacturer.publishable_as_manufacturer
                  ? "text-[var(--fg-tertiary)]"
                  : "text-[var(--warn)]",
              )}
            >
              {summary.manufacturer.looks_like_a_distributor
                ? "Looks like a distributor or buying co-op, so it is recorded and echoed but not published as the manufacturer without the approved master."
                : "Safe to propose. The client requires exact casing and legal suffixes from the approved master, so this is a proposal rather than a verified name."}
            </p>
          </div>
        </div>
      </section>

      <ManufacturerSpecifications
        specifications={manufacturerSpecifications}
        headingId="enrich-manufacturer-specifications-heading"
      />

      {summary.notes.length > 0 ? (
        <section aria-labelledby="enrich-notes-heading">
          <h3 id="enrich-notes-heading" className="overline">
            Read this before reading the values
          </h3>
          <ul className="mt-4 flex list-disc flex-col gap-2.5 pl-5 text-sm text-[var(--fg-tertiary)]">
            {summary.notes.map((note, index) => (
              <li key={`${note.slice(0, 24)}-${index}`}>{note}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby="enrich-next-heading">
        <h3 id="enrich-next-heading" className="overline">
          What to do with it
        </h3>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Panel className="p-5">
            <h4 className="text-sm font-medium">Take the delivery file</h4>
            <p className="mt-2 text-meta text-[var(--fg-tertiary)]">
              {delivery.populated} of {delivery.columns} columns populated, {delivery.blank} left
              empty because nothing evidenced them, {delivery.withheld} withheld because a value
              existed and was not allowed to publish. Written when the run finished, so downloading
              costs nothing.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <a
                href={`/api/enrich/delivery?sku=${encodeURIComponent(data.slug)}&output=xlsx`}
                download
                className="btn btn-primary min-h-11"
              >
                Excel workbook
              </a>
              <a
                href={`/api/enrich/delivery?sku=${encodeURIComponent(data.slug)}&output=csv`}
                download
                className="btn btn-quiet min-h-11"
              >
                CSV
              </a>
            </div>
            <p className="mono mt-4 break-all text-meta text-[var(--fg-quiet)]">
              content hash {shortHash(delivery.content_hash, 32)}…
            </p>
          </Panel>

          <Panel className="p-5">
            <h4 className="text-sm font-medium">Resolve what it could not settle</h4>
            <p className="mt-2 text-meta text-[var(--fg-tertiary)]">
              {queue.pending} of {queue.total} values need a decision
              {queue.blocking_failures > 0
                ? `, and ${queue.blocking_failures} carry a blocking validation failure`
                : ""}
              . With no calibration data yet, everything queues — that is the correct opening state
              rather than a failure.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <Link href={reviewHref(data.sku)} className="btn btn-primary min-h-11">
                Open in Resolve
              </Link>
              <Link href={certificateHref(data.sku)} className="btn btn-quiet min-h-11">
                View the certificate
              </Link>
            </div>
            <p className="mt-4 text-meta text-[var(--fg-quiet)]">
              Also replayable from Process, and counted on Operations and Intelligence.
            </p>
          </Panel>
        </div>
      </section>
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "default" | "warn" | "fail" | "pass";
}) {
  const toneClass = {
    default: "text-[var(--fg)]",
    pass: "text-[var(--pass)]",
    warn: "text-[var(--warn)]",
    fail: "text-[var(--fail)]",
  }[tone];

  return (
    <div className="border-l border-[var(--hairline-strong)] pl-4">
      <dt className="overline">{label}</dt>
      <dd className={clsx("mt-2 text-xl font-medium tabular-nums", toneClass)}>{value}</dd>
      <dd className="mono mt-1 break-words text-meta text-[var(--fg-tertiary)]">{hint}</dd>
    </div>
  );
}
