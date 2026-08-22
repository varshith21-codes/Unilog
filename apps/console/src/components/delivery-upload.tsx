"use client";

/**
 * The upload form and its result.
 *
 * A client component because the whole interaction is local: pick files, post them, take the
 * download. It posts to this app's own route handler rather than to FastAPI, so the API URL stays
 * on the server.
 *
 * The result rendering carries most of the weight here, and the reason is the shape of the output.
 * A caller who uploads a six-column item master and gets back a 252-column file with 224 columns
 * empty will read it as broken unless the page says otherwise, so the empty columns and the
 * withheld cells are reported as findings rather than left to be discovered. Sparse is the correct
 * answer — the client's own ground truth leaves 173 of 252 columns blank — and a file that filled
 * everything would be fabricated.
 */

import clsx from "clsx";
import { useId, useRef, useState } from "react";

import { KeyValue, Overline } from "@/components/primitives";
import type {
  DeliveryFormatView,
  DeliveryRunSummary,
  MissingInputColumns,
} from "@/lib/delivery";

type OutputFormat = "xlsx" | "csv" | "json";

type Outcome =
  | { kind: "idle" }
  | { kind: "running" }
  | {
      kind: "downloaded";
      filename: string;
      bytes: number;
      rows: number;
      populated: number;
      total: number;
      withheld: number;
      compliant: boolean;
      contentHash: string;
    }
  | { kind: "preview"; summary: DeliveryRunSummary }
  | { kind: "rejected"; columns: MissingInputColumns }
  | { kind: "failed"; status: number | null; message: string };

const OUTPUTS: { value: OutputFormat; label: string; detail: string }[] = [
  {
    value: "xlsx",
    label: "Excel workbook",
    detail:
      "The delivery sheet plus its provenance record. Every cell is written as text, so Excel " +
      "cannot turn 50-1/4 into a date or drop the leading zero from a part number.",
  },
  {
    value: "csv",
    label: "CSV",
    detail: "Byte-for-byte what the pipeline writes to disk.",
  },
  {
    value: "json",
    label: "Preview only",
    detail: "Run it and report the result here. Nothing is downloaded.",
  },
];

export function DeliveryUpload({ contract }: { contract: DeliveryFormatView }) {
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const [output, setOutput] = useState<OutputFormat>("xlsx");
  const [master, setMaster] = useState<File | null>(null);
  const [documents, setDocuments] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);

  const formRef = useRef<HTMLFormElement>(null);
  const masterInput = useRef<HTMLInputElement>(null);
  const masterId = useId();
  const documentsId = useId();
  const masterHeadingId = `${masterId}-heading`;
  const masterGuidanceId = `${masterId}-guidance`;
  const masterStatusId = `${masterId}-status`;
  const documentsHeadingId = `${documentsId}-heading`;
  const documentsGuidanceId = `${documentsId}-guidance`;
  const documentsStatusId = `${documentsId}-status`;
  const running = outcome.kind === "running";
  const outputChoices = OUTPUTS.filter((choice) =>
    contract.upload.outputs.includes(choice.value),
  );

  function pickMaster(files: FileList | null) {
    const file = files?.[0] ?? null;
    if (!file) return;
    setMaster(file);
    if (masterInput.current && files) masterInput.current.files = files;
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!master || running) return;

    const data = new FormData(event.currentTarget);
    // Unchecked boxes are simply absent from FormData, and the API reads them as booleans. Sending
    // them explicitly means "off" is stated rather than inferred from a missing field.
    for (const name of ["read_descriptions", "include_audit", "classified_only"]) {
      const input = event.currentTarget.elements.namedItem(name);
      data.set(
        name,
        input instanceof HTMLInputElement && input.checked ? "true" : "false",
      );
    }
    if (!data.get("limit")) data.delete("limit");
    if (!data.get("mpns")) data.delete("mpns");
    if (documents.length === 0) data.delete("documents");

    setOutcome({ kind: "running" });

    try {
      const response = await fetch("/api/delivery/export", { method: "POST", body: data });

      if (!response.ok) {
        setOutcome(await readFailure(response));
        return;
      }

      if (output === "json") {
        setOutcome({ kind: "preview", summary: (await response.json()) as DeliveryRunSummary });
        return;
      }

      const blob = await response.blob();
      const filename = filenameFrom(response) ?? `delivery.${output}`;
      triggerDownload(blob, filename);
      setOutcome({
        kind: "downloaded",
        filename,
        bytes: blob.size,
        rows: Number(response.headers.get("x-axiom-rows") ?? 0),
        populated: Number(response.headers.get("x-axiom-columns-populated") ?? 0),
        total: Number(response.headers.get("x-axiom-columns-total") ?? contract.columns),
        withheld: Number(response.headers.get("x-axiom-withheld") ?? 0),
        compliant: response.headers.get("x-axiom-compliant") === "true",
        contentHash: response.headers.get("x-axiom-content-hash") ?? "",
      });
    } catch (cause) {
      setOutcome({
        kind: "failed",
        status: null,
        message: cause instanceof Error ? cause.message : String(cause),
      });
    }
  }

  return (
    <>
      <form
        ref={formRef}
        onSubmit={submit}
        aria-busy={running}
        className="publish-form grid items-start gap-8 lg:grid-cols-12 lg:gap-10"
      >
        <div className="flex min-w-0 flex-col gap-10 lg:col-span-8">
          <section aria-labelledby={masterHeadingId} className="hairline-t pt-5">
            <StepHeading
              number="01"
              id={masterHeadingId}
              title="Stage the item master"
              status="Required"
            />
            <p id={masterGuidanceId} className="mt-3 max-w-prose text-sm text-[var(--fg-tertiary)]">
              {contract.upload.accepts.join(", ")}. The {contract.input_columns.length} input
              columns have to be present and spelled exactly, because the delivery format joins
              back to your file on{" "}
              <code className="mono text-[var(--fg-secondary)]">{contract.join_key}</code>.
            </p>

            <div
              onDragEnter={(event) => {
                event.preventDefault();
                if (!running) setDragging(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                  setDragging(false);
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (!running) pickMaster(event.dataTransfer.files);
              }}
              aria-disabled={running}
              className={clsx(
                "mt-5 border bg-[var(--surface)] p-5 sm:p-6",
                dragging
                  ? "border-[var(--accent)] bg-[var(--accent-quiet)]"
                  : "border-[var(--border-control)]",
              )}
            >
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <label htmlFor={masterId} className="text-sm font-medium text-[var(--fg)]">
                    {dragging ? "Release to stage this item master" : "Choose a file or drop it here"}
                  </label>
                  <p className="mt-1 text-meta text-[var(--fg-quiet)]">
                    Up to {formatBytes(contract.upload.max_bytes)} ·{" "}
                    {contract.upload.max_rows.toLocaleString()} rows per request
                  </p>
                </div>
                <input
                  ref={masterInput}
                  id={masterId}
                  name="file"
                  type="file"
                  required
                  disabled={running}
                  accept={contract.upload.accepts.join(",")}
                  aria-describedby={`${masterGuidanceId} ${masterStatusId}`}
                  onChange={(event) => setMaster(event.target.files?.[0] ?? null)}
                  className="mono block min-h-11 min-w-0 max-w-full text-sm text-[var(--fg-secondary)] file:mr-3 file:rounded-md file:border file:border-[var(--border-control)] file:bg-[var(--surface-raised)] file:px-3 file:py-2 file:text-sm file:font-medium file:text-[var(--fg)] hover:file:bg-[var(--surface-hover)] active:file:opacity-80"
                />
              </div>

              <div
                id={masterStatusId}
                aria-live="polite"
                className={clsx(
                  "mt-5 border-l-2 py-1 pl-4",
                  master ? "border-[var(--accent)]" : "border-[var(--hairline-strong)]",
                )}
              >
                <p className="overline">Selection</p>
                {master ? (
                  <p className="mono mt-1 break-words text-[var(--fg)]">
                    {master.name} · {formatBytes(master.size)} · staged
                  </p>
                ) : (
                  <p className="mt-1 text-sm text-[var(--fg-quiet)]">
                    No item master staged. Export remains locked.
                  </p>
                )}
              </div>
            </div>

            <div className="mt-6">
              <Overline>Required columns</Overline>
              <ul className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2 xl:grid-cols-3">
                {contract.input_columns.map((column) => (
                  <li
                    key={column}
                    className="border-l border-[var(--hairline-strong)] py-1 pl-3"
                  >
                    <code className="mono break-words text-[var(--fg-secondary)]">{column}</code>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section aria-labelledby={documentsHeadingId} className="hairline-t pt-5">
            <StepHeading
              number="02"
              id={documentsHeadingId}
              title="Attach source evidence"
              status="Optional · multiple"
            />
            <p
              id={documentsGuidanceId}
              className="mt-3 max-w-prose text-sm text-[var(--fg-tertiary)]"
            >
              Datasheets, spec sheets or price pages as PDF, HTML or text. This is what fills the
              attribute grid, and each value read from one cites the specification line or table
              cell it came from. Without documents there is nothing to extract from, so the grid
              comes back carrying its labels and few values: the row is structurally complete and
              specification-poor.
            </p>

            <div className="mt-5 border border-[var(--hairline-strong)] bg-[var(--surface)] p-5 sm:p-6">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <label htmlFor={documentsId} className="text-sm font-medium text-[var(--fg)]">
                    Manufacturer documents
                  </label>
                  <p className="mt-1 text-meta text-[var(--fg-quiet)]">
                    Up to {contract.upload.max_documents} ·{" "}
                    {formatBytes(contract.upload.max_document_bytes)} each
                  </p>
                </div>
                <input
                  id={documentsId}
                  name="documents"
                  type="file"
                  multiple
                  disabled={running}
                  aria-describedby={`${documentsGuidanceId} ${documentsStatusId}`}
                  onChange={(event) => setDocuments([...(event.target.files ?? [])])}
                  className="mono block min-h-11 min-w-0 max-w-full text-sm text-[var(--fg-secondary)] file:mr-3 file:rounded-md file:border file:border-[var(--border-control)] file:bg-[var(--surface-raised)] file:px-3 file:py-2 file:text-sm file:font-medium file:text-[var(--fg)] hover:file:bg-[var(--surface-hover)] active:file:opacity-80"
                />
              </div>

              <div id={documentsStatusId} aria-live="polite" className="hairline-t mt-5 pt-4">
                {documents.length > 0 ? (
                  <>
                    <p className="overline">
                      {documents.length} document{documents.length === 1 ? "" : "s"} staged
                    </p>
                    <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                      {documents.map((doc, index) => (
                        <li
                          key={`${doc.name}-${doc.size}-${doc.lastModified}-${index}`}
                          className="mono min-w-0 break-words text-[var(--fg-secondary)]"
                        >
                          {doc.name} · {formatBytes(doc.size)}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="text-sm text-[var(--fg-quiet)]">
                    No documents attached. The export will contain structural columns and only
                    values evidenced by the item master.
                  </p>
                )}
              </div>
            </div>
          </section>
        </div>

        <aside
          aria-labelledby="export-configuration-heading"
          className="border border-[var(--hairline-strong)] bg-[var(--surface-raised)] shadow-sm lg:sticky lg:top-20 lg:col-span-4"
        >
          <header className="border-b border-[var(--hairline-strong)] p-5">
            <div className="flex items-center justify-between gap-4">
              <span className="mono text-[var(--accent)]">03</span>
              <span className="overline">Publish control</span>
            </div>
            <h2 id="export-configuration-heading" className="mt-3 text-lg font-medium">
              Export configuration
            </h2>
            <p className="mt-1 text-sm text-[var(--fg-tertiary)]">
              {contract.format} · {contract.columns.toLocaleString()} delivery columns
            </p>
          </header>

          <fieldset
            disabled={running}
            className="border-b border-[var(--hairline)] py-4 disabled:opacity-60"
          >
            <legend className="overline px-5">Output</legend>
            <div className="mt-2">
              {outputChoices.map((choice) => {
                const selected = output === choice.value;
                return (
                  <label
                    key={choice.value}
                    className={clsx(
                      "flex min-h-11 cursor-pointer items-start gap-3 border-l-2 px-5 py-3 hover:bg-[var(--surface-hover)] active:opacity-80",
                      selected
                        ? "border-[var(--accent)] bg-[var(--accent-quiet)]"
                        : "border-transparent",
                    )}
                  >
                    <input
                      type="radio"
                      name="output"
                      value={choice.value}
                      checked={selected}
                      onChange={() => setOutput(choice.value)}
                      className="mt-1 accent-[var(--accent)]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="text-sm font-medium text-[var(--fg)]">{choice.label}</span>
                        {selected ? (
                          <span className="mono text-[var(--accent)]">Selected</span>
                        ) : null}
                      </span>
                      <span className="mt-1 block text-meta text-[var(--fg-tertiary)]">
                        {choice.detail}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <fieldset
            disabled={running}
            className="border-b border-[var(--hairline)] p-5 disabled:opacity-60"
          >
            <legend className="overline">Rows</legend>
            <div className="mt-3 flex flex-col gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm text-[var(--fg-secondary)]">Limit</span>
                <input
                  name="limit"
                  type="number"
                  min={1}
                  placeholder="all rows"
                  className="mono min-h-11"
                />
                <span className="text-meta text-[var(--fg-quiet)]">
                  Contract ceiling: {contract.upload.max_rows.toLocaleString()} rows
                </span>
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm text-[var(--fg-secondary)]">Only these part numbers</span>
                <input
                  name="mpns"
                  type="text"
                  placeholder="PDSH4816AF, WDTS7024RZ"
                  className="mono min-h-11"
                />
                <span className="text-meta text-[var(--fg-quiet)]">Comma-separated MPNs</span>
              </label>
            </div>
          </fieldset>

          <fieldset
            disabled={running}
            className="border-b border-[var(--hairline)] p-5 disabled:opacity-60"
          >
            <legend className="overline">Behaviour</legend>
            <div className="mt-2 flex flex-col">
              <Toggle
                name="read_descriptions"
                defaultChecked
                label="Read attributes from the description"
              />
              <Toggle name="include_audit" defaultChecked label="Include the provenance sheets" />
              <Toggle name="classified_only" label="Drop rows that would not classify" />
            </div>
          </fieldset>

          <div className="p-5">
            <button
              type="submit"
              disabled={running || !master}
              data-loading={running ? "true" : "false"}
              className="btn btn-primary min-h-11 w-full"
            >
              {running
                ? "Processing delivery…"
                : output === "json"
                  ? "Run delivery preview"
                  : "Generate delivery file"}
            </button>
            <p
              role="status"
              aria-live="polite"
              aria-atomic="true"
              className="mt-3 text-center text-meta text-[var(--fg-tertiary)]"
            >
              {outcomeStatus(outcome, master, contract)}
            </p>
          </div>
        </aside>
      </form>

      <Result outcome={outcome} contract={contract} />
    </>
  );
}

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

function Toggle({
  name,
  label,
  defaultChecked = false,
}: {
  name: string;
  label: string;
  defaultChecked?: boolean;
}) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3 border-b border-[var(--hairline)] py-2 text-sm text-[var(--fg-secondary)] last:border-b-0 hover:text-[var(--fg)] active:opacity-80">
      <input type="checkbox" name={name} defaultChecked={defaultChecked} />
      <span>{label}</span>
    </label>
  );
}

function outcomeStatus(
  outcome: Outcome,
  master: File | null,
  contract: DeliveryFormatView,
): string {
  if (outcome.kind === "running") {
    return "Running the projection. A thousand rows takes about twenty seconds.";
  }
  if (outcome.kind === "downloaded") {
    return `Delivery file ready. ${outcome.filename} was downloaded.`;
  }
  if (outcome.kind === "preview") {
    return `Preview complete. ${outcome.summary.batch.rows.toLocaleString()} rows produced; nothing was downloaded.`;
  }
  if (outcome.kind === "rejected") {
    return "Upload rejected. Required item-master columns are missing.";
  }
  if (outcome.kind === "failed") {
    return outcome.status ? `Export failed with status ${outcome.status}.` : "Export failed.";
  }
  return master
    ? `${contract.format} · ${contract.columns} columns · ready to generate`
    : "Pick an item master to start.";
}

// ------------------------------------------------------------------ results

function Result({ outcome, contract }: { outcome: Outcome; contract: DeliveryFormatView }) {
  if (outcome.kind === "idle" || outcome.kind === "running") return null;

  if (outcome.kind === "rejected") {
    const { columns } = outcome;
    return (
      <section
        aria-labelledby="delivery-rejected-heading"
        className="animate-rise mt-10 border-y border-[var(--hairline-strong)] py-6"
      >
        <ResultHeading
          eyebrow="Input rejected · nothing processed"
          title="This does not look like a Unilog item master"
          id="delivery-rejected-heading"
          tone="fail"
          detail={
            <>
              The delivery format joins on{" "}
              <code className="mono text-[var(--fg-secondary)]">{contract.join_key}</code>, so all{" "}
              {contract.input_columns.length} input columns have to be present and spelled exactly.
              Nothing was processed.
            </>
          }
        />
        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <div className="border-l-2 border-[var(--fail)] pl-4">
            <Overline>Missing</Overline>
            <ul className="mt-3 flex flex-col gap-2">
              {columns.missing.map((column) => (
                <li key={column}>
                  <code className="mono break-words text-[var(--fail)]">{column}</code>
                </li>
              ))}
            </ul>
          </div>
          <div className="border-l border-[var(--hairline-strong)] pl-4">
            <Overline>Found in your file</Overline>
            {columns.found.length > 0 ? (
              <ul className="mt-3 flex flex-col gap-2">
                {columns.found.map((column) => (
                  <li key={column}>
                    <code className="mono break-words text-[var(--fg-tertiary)]">{column}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-[var(--fg-quiet)]">No headers were readable.</p>
            )}
          </div>
        </div>
      </section>
    );
  }

  if (outcome.kind === "failed") {
    return (
      <section
        aria-labelledby="delivery-failed-heading"
        className="animate-rise mt-10 border-y border-[var(--fail)] bg-[var(--fail-quiet)] py-6"
      >
        <div className="px-5 sm:px-6">
          <ResultHeading
            eyebrow="Export interrupted"
            title={outcome.status ? `Export failed (${outcome.status})` : "Export failed"}
            id="delivery-failed-heading"
            tone="fail"
            detail={outcome.message}
          />
        </div>
      </section>
    );
  }

  if (outcome.kind === "downloaded") {
    const blank = outcome.total - outcome.populated;
    return (
      <section aria-labelledby="delivery-ready-heading" className="animate-rise mt-10">
        <ResultHeading
          eyebrow="Export complete · download started"
          title="Delivery file ready"
          id="delivery-ready-heading"
          tone="pass"
          detail={
            <>
              Downloaded as{" "}
              <code className="mono break-words text-[var(--fg-secondary)]">{outcome.filename}</code>{" "}
              · {formatBytes(outcome.bytes)}
            </>
          }
        />
        <MetricBand>
          <Metric label="Rows" value={outcome.rows.toLocaleString()} hint="records written" />
          <Metric
            label="Columns populated"
            value={`${outcome.populated}/${outcome.total}`}
            hint={`${blank} left empty`}
          />
          <Metric
            label="Withheld cells"
            value={outcome.withheld.toLocaleString()}
            hint="had a value, not allowed to publish"
            tone={outcome.withheld > 0 ? "warn" : "default"}
          />
          <Metric
            label="Character limits"
            value={outcome.compliant ? "PASS" : "FAIL"}
            hint="declared min/max and casing"
            tone={outcome.compliant ? "pass" : "fail"}
          />
        </MetricBand>
        <div className="mt-6 border-l-2 border-[var(--accent)] pl-4">
          <p className="max-w-3xl text-sm text-[var(--fg-tertiary)]">
            {blank} of {outcome.total} columns came back empty, and that is the correct answer rather
            than a gap: a column is left blank when nothing in your input or the attached documents
            evidenced a value for it. Withheld cells are refusals — a value existed and was not
            allowed to publish. Both are itemised cell by cell on the workbook&rsquo;s Provenance and
            Withheld sheets.
          </p>
          {outcome.contentHash ? (
            <p className="mono mt-3 break-all text-[var(--fg-quiet)]">
              content hash {outcome.contentHash.slice(0, 32)}…
            </p>
          ) : null}
        </div>
      </section>
    );
  }

  return <Preview summary={outcome.summary} contract={contract} />;
}

function Preview({
  summary,
  contract,
}: {
  summary: DeliveryRunSummary;
  contract: DeliveryFormatView;
}) {
  const { batch, export: report, source } = summary;
  const columns = summary.columns_populated.map((entry) => entry.column);
  const perRowHeadingId = useId();
  const deliveryCellsHeadingId = useId();

  return (
    <section aria-labelledby="delivery-preview-heading" className="animate-rise mt-10">
      <ResultHeading
        eyebrow="Projection complete · preview mode"
        title="Run complete"
        id="delivery-preview-heading"
        tone="pass"
        detail={
          <>
            <code className="mono break-words text-[var(--fg-secondary)]">{source.filename}</code> ·{" "}
            {report.format} · nothing was downloaded
          </>
        }
      />

      <MetricBand>
        <Metric
          label="Rows produced"
          value={batch.rows.toLocaleString()}
          hint={`of ${source.rows_in_file.toLocaleString()} in the file`}
        />
        <Metric
          label="Rows skipped"
          value={batch.skipped.toLocaleString()}
          hint="unidentified or unclassified"
          tone={batch.skipped > 0 ? "warn" : "default"}
        />
        <Metric
          label="Columns populated"
          value={`${report.columns_populated}/${contract.columns}`}
          hint="at least once across the batch"
        />
        <Metric
          label="Character limits"
          value={report.compliant ? "PASS" : "FAIL"}
          hint={`${report.constraint_violations} violation(s)`}
          tone={report.compliant ? "pass" : "fail"}
        />
      </MetricBand>

      <div className="mt-8 grid border-y border-[var(--hairline-strong)] md:grid-cols-3 md:divide-x md:divide-[var(--hairline)]">
        <section aria-labelledby="preview-provenance-heading" className="p-5 md:p-6">
          <h3 id="preview-provenance-heading" className="overline">
            Where the values came from
          </h3>
          <dl className="mt-3 flex flex-col gap-3">
            <KeyValue label="From the description" mono>
              {batch.from_description.extracted} kept, {batch.from_description.refused} refused
            </KeyValue>
            <KeyValue label="From documents" mono>
              {batch.from_documents.extracted} kept, {batch.from_documents.refused} refused
            </KeyValue>
          </dl>
          <div className="hairline-t mt-5 pt-4">
            <Overline>Cells by provenance</Overline>
            {Object.keys(report.provenance).length > 0 ? (
              <dl className="mt-2">
                {Object.entries(report.provenance)
                  .sort(([, a], [, b]) => b - a)
                  .map(([name, count]) => (
                    <div
                      key={name}
                      className="flex items-baseline justify-between gap-4 border-b border-[var(--hairline)] py-2 last:border-b-0"
                    >
                      <dt className="text-sm text-[var(--fg-tertiary)]">{name}</dt>
                      <dd className="mono text-[var(--fg)]">{count}</dd>
                    </div>
                  ))}
              </dl>
            ) : (
              <p className="mt-3 text-sm text-[var(--fg-quiet)]">No populated cells reported.</p>
            )}
          </div>
        </section>

        <section
          aria-labelledby="preview-documents-heading"
          className="border-t border-[var(--hairline)] p-5 md:border-t-0 md:p-6"
        >
          <h3 id="preview-documents-heading" className="overline">
            Documents read
          </h3>
          {summary.documents.length > 0 ? (
            <ul className="mt-3 flex flex-col gap-3">
              {summary.documents.map((doc) => (
                <li
                  key={doc.document_id}
                  className="mono break-words border-l border-[var(--hairline-strong)] pl-3 text-[var(--fg-tertiary)]"
                >
                  {doc.document_id} · {doc.pages} page(s) · {doc.tables} table(s) · {doc.parser}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-[var(--fg-quiet)]">None attached.</p>
          )}
          <div className="hairline-t mt-5 pt-4">
            <Overline>Classification</Overline>
            {Object.keys(batch.classification).length > 0 ? (
              <dl className="mt-2">
                {Object.entries(batch.classification).map(([method, count]) => (
                  <div
                    key={method}
                    className="flex justify-between gap-3 border-b border-[var(--hairline)] py-2 text-sm last:border-b-0"
                  >
                    <dt className="text-[var(--fg-tertiary)]">{method}</dt>
                    <dd className="mono text-[var(--fg-secondary)]">{count}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-3 text-sm text-[var(--fg-quiet)]">
                No classification methods reported.
              </p>
            )}
          </div>
        </section>

        <section
          aria-labelledby="preview-notes-heading"
          className="border-t border-[var(--hairline)] p-5 md:border-t-0 md:p-6"
        >
          <h3 id="preview-notes-heading" className="overline">
            Read this before reading the file
          </h3>
          {summary.notes.length > 0 ? (
            <ul className="mt-3 flex list-disc flex-col gap-2.5 pl-4 text-sm text-[var(--fg-tertiary)]">
              {summary.notes.map((note, index) => (
                <li key={`${note}-${index}`}>{note}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-[var(--fg-quiet)]">No run notes were reported.</p>
          )}
          {source.dead_columns.length > 0 ? (
            <p className="hairline-t mt-5 pt-4 text-sm text-[var(--warn)]">
              Carries no data: {source.dead_columns.join(", ")}. A column that is entirely sentinel
              values is one the supplier believes they are sending and are not.
            </p>
          ) : null}
        </section>
      </div>

      <section aria-labelledby={perRowHeadingId} className="mt-8 border-y border-[var(--hairline-strong)]">
        <header className="border-b border-[var(--hairline)] px-5 py-4">
          <h3 id={perRowHeadingId} className="overline">
            Per row
          </h3>
          <p className="mt-1 text-meta text-[var(--fg-quiet)]">
            Classification path and accepted source-cell counts for every item-master row.
          </p>
        </header>
        <div
          className="scroll-x"
          tabIndex={0}
          role="region"
          aria-labelledby={perRowHeadingId}
        >
          <table className="w-full min-w-max text-sm">
            <caption className="sr-only">
              Per-row delivery outcome, classification method and evidence-source counts
            </caption>
            <thead className="table-head">
              <tr>
                <th scope="col" className="px-5 py-2.5 text-left font-medium">Part number</th>
                <th scope="col" className="px-5 py-2.5 text-left font-medium">Class</th>
                <th scope="col" className="px-5 py-2.5 text-left font-medium">How</th>
                <th scope="col" className="px-5 py-2.5 text-right font-medium">Cells</th>
                <th scope="col" className="px-5 py-2.5 text-right font-medium">Description</th>
                <th scope="col" className="px-5 py-2.5 text-right font-medium">Documents</th>
              </tr>
            </thead>
            <tbody className="mono">
              {summary.rows.length > 0 ? (
                summary.rows.map((row, index) => (
                  <tr key={`${row.mpn ?? "unidentified"}-${index}`} className="hover:bg-[var(--surface-hover)]">
                    <th scope="row" className="px-5 py-2 text-left font-normal">
                      {row.mpn ?? "—"}
                    </th>
                    <td className="px-5 py-2 text-[var(--fg-tertiary)]">{row.class_code ?? "—"}</td>
                    <td
                      className={clsx(
                        "px-5 py-2",
                        row.skipped ? "text-[var(--warn)]" : "text-[var(--fg-tertiary)]",
                      )}
                    >
                      {row.skipped ? `skipped: ${row.skipped}` : row.method}
                    </td>
                    <td className="px-5 py-2 text-right">{row.populated}</td>
                    <td
                      className={clsx(
                        "px-5 py-2 text-right",
                        row.from_description > 0 ? "text-[var(--pass)]" : "figure-zero",
                      )}
                    >
                      {row.from_description}
                    </td>
                    <td
                      className={clsx(
                        "px-5 py-2 text-right",
                        row.from_documents > 0 ? "text-[var(--pass)]" : "figure-zero",
                      )}
                    >
                      {row.from_documents}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-[var(--fg-quiet)]">
                    No row outcomes were produced.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {summary.preview.length > 0 ? (
        <section
          aria-labelledby={deliveryCellsHeadingId}
          className="mt-8 border-y border-[var(--hairline-strong)]"
        >
          <header className="border-b border-[var(--hairline)] px-5 py-4">
            <h3 id={deliveryCellsHeadingId} className="overline">
              Delivery cells
            </h3>
            <p className="mt-1 text-meta text-[var(--fg-quiet)]">
              The {columns.length} columns something populated, of {contract.columns}. Empty columns
              are hidden here to keep the grid readable; the file has all {contract.columns}, in the
              client&rsquo;s order.
            </p>
          </header>
          <div
            className="scroll-x max-h-96 overflow-y-auto"
            tabIndex={0}
            role="region"
            aria-labelledby={deliveryCellsHeadingId}
          >
            <table className="min-w-max text-sm">
              <caption className="sr-only">
                Populated delivery-cell preview in the client delivery-column order
              </caption>
              <thead className="table-head sticky top-0 z-10">
                <tr>
                  {columns.map((column) => (
                    <th key={column} scope="col" className="px-3 py-2.5 text-left font-medium">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="mono">
                {summary.preview.map((row, index) => (
                  <tr key={index} className="hover:bg-[var(--surface-hover)]">
                    {columns.map((column) => (
                      <td key={column} className="px-3 py-2 whitespace-nowrap">
                        {row[column] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </section>
  );
}

function ResultHeading({
  eyebrow,
  title,
  id,
  detail,
  tone = "default",
}: {
  eyebrow: string;
  title: string;
  id: string;
  detail: React.ReactNode;
  tone?: "default" | "pass" | "fail";
}) {
  const toneClass = {
    default: "text-[var(--fg-quiet)]",
    pass: "text-[var(--pass)]",
    fail: "text-[var(--fail)]",
  }[tone];

  return (
    <header>
      <p className={clsx("overline", toneClass)}>{eyebrow}</p>
      <h2 id={id} className="mt-2 text-xl font-medium tracking-[var(--tracking-heading)]">
        {title}
      </h2>
      <p className="mt-2 max-w-3xl text-sm text-[var(--fg-tertiary)]">{detail}</p>
    </header>
  );
}

function MetricBand({ children }: { children: React.ReactNode }) {
  return (
    <dl className="mt-6 grid grid-cols-2 gap-x-5 gap-y-7 border-y border-[var(--hairline-strong)] py-6 lg:grid-cols-4 lg:gap-x-6">
      {children}
    </dl>
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
      <dd className="mt-1 text-meta text-[var(--fg-tertiary)]">{hint}</dd>
    </div>
  );
}

// ------------------------------------------------------------------ helpers

async function readFailure(response: Response): Promise<Outcome> {
  let detail: unknown;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    detail = null;
  }

  if (
    detail !== null &&
    typeof detail === "object" &&
    (detail as { error?: string }).error === "missing_input_columns"
  ) {
    return { kind: "rejected", columns: detail as MissingInputColumns };
  }

  const message =
    typeof detail === "string"
      ? detail
      : detail !== null && typeof detail === "object" && "message" in detail
        ? String((detail as { message: unknown }).message)
        : `The API returned ${response.status} ${response.statusText}.`;

  return { kind: "failed", status: response.status, message };
}

function filenameFrom(response: Response): string | null {
  const header = response.headers.get("content-disposition") ?? "";

  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  if (encoded !== undefined) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      // A malformed percent-escape should not lose the download; fall through to the plain form.
    }
  }

  return /filename="([^"]+)"/i.exec(header)?.[1] ?? null;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Revoked on a delay: Safari cancels an in-flight download if the blob URL dies immediately.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}
