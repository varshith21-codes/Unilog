import clsx from "clsx";

import { AlertIcon, CheckIcon, Overline, Panel, SectionHeading } from "@/components/primitives";
import { humanise } from "@/lib/format";
import type { Claim, FormalCheck, GeneratedCopy } from "@/lib/types";

/**
 * Generated copy, shown only ever beside its claim check.
 *
 * These two are one artifact. The argument for letting a model write product descriptions at all
 * is that every checkable assertion in the output was verified against an attribute that was
 * already publishable — so displaying the prose without the verdict would be making a claim the
 * system does not support, and displaying it *as* verified when the check failed would be worse.
 *
 * Failed copy is therefore rendered, not hidden. A merchandiser needs to see what the model tried
 * to say and which sentence was rejected; suppressing it entirely turns a legible failure into a
 * mysterious blank.
 */
export function GeneratedCopyPanel({ copy }: { copy: GeneratedCopy }) {
  const failing = copy.claims.filter((claim) => claim.verdict !== "supported");
  const { claim_check: check } = copy;
  const formal = copy.formal_check ?? null;

  // Which gate to name in the pill. Two gates run and either can withhold copy, so a single
  // "Blocked" would leave a merchandiser guessing which one to go and read.
  const blockedBy = !copy.published
    ? formal && !formal.passed
      ? formal.error
        ? "Not verified"
        : "Contradiction"
      : "Blocked"
    : null;

  return (
    <section>
      <SectionHeading
        title="Generated copy"
        detail="Written from verified attributes only, then checked claim by claim."
        action={
          <span className={clsx("pill", copy.published ? "pill-pass" : "pill-fail")}>
            {copy.published ? <CheckIcon /> : <AlertIcon />}
            {copy.published
              ? formal?.conclusive
                ? "Checked and formally verified"
                : "Claim check passed"
              : blockedBy}
          </span>
        }
      />

      {copy.error ? (
        <Panel className="mt-6 p-6">
          <p className="text-sm text-[var(--fg-secondary)]">{copy.error}</p>
        </Panel>
      ) : (
        <div className="mt-6 grid gap-6 lg:grid-cols-12">
          {/* ------------------------------------------------------ the copy */}
          <Panel className="p-7 lg:col-span-7">
            <Overline>Headline</Overline>
            <h3 className="mt-2 text-xl font-medium tracking-[var(--tracking-heading)]">
              {copy.headline}
            </h3>

            {copy.short_description ? (
              <>
                <Overline className="mt-7 block">Short description</Overline>
                <p className="mt-2 max-w-[64ch] text-body text-[var(--fg-secondary)]">
                  {copy.short_description}
                </p>
              </>
            ) : null}

            {copy.long_description ? (
              <>
                <Overline className="mt-7 block">Long description</Overline>
                <div className="mt-2 flex max-w-[68ch] flex-col gap-3">
                  {copy.long_description
                    .split(/\n+/)
                    .filter((paragraph) => paragraph.trim())
                    .map((paragraph) => (
                      <p key={paragraph} className="text-sm text-[var(--fg-secondary)]">
                        {paragraph.trim()}
                      </p>
                    ))}
                </div>
              </>
            ) : null}

            {copy.bullets.length > 0 ? (
              <>
                <Overline className="mt-7 block">Specifications</Overline>
                <ul className="mt-2 flex flex-col gap-1.5">
                  {copy.bullets.map((bullet) => (
                    <li
                      key={bullet}
                      className="flex gap-2.5 text-sm text-[var(--fg-secondary)]"
                    >
                      <span aria-hidden className="text-[var(--fg-quiet)]">
                        —
                      </span>
                      {bullet}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            <p className="hairline-t mt-7 pt-5 text-meta text-[var(--fg-quiet)]">
              {copy.model_id ? (
                <>
                  <span className="mono">{copy.model_id}</span> ({copy.model_tier}) ·{" "}
                </>
              ) : null}
              <span className="mono">{copy.prompt_version}</span> · attempt {copy.attempts}
            </p>
          </Panel>

          {/* ------------------------------------------------------ the check */}
          <Panel className="p-7 lg:col-span-5">
            <Overline>Claim check</Overline>

            <p className="mt-3 text-sm text-[var(--fg-secondary)]">
              <span className="tabular-nums text-[var(--fg)]">{check.claims}</span> checkable
              assertions found.{" "}
              <span className="tabular-nums text-[var(--pass)]">{check.supported}</span>{" "}
              traced to a verified attribute
              {check.unsupported > 0 ? (
                <>
                  ,{" "}
                  <span className="tabular-nums text-[var(--fail)]">{check.unsupported}</span>{" "}
                  did not
                </>
              ) : null}
              {check.banned > 0 ? (
                <>
                  ,{" "}
                  <span className="tabular-nums text-[var(--fail)]">{check.banned}</span>{" "}
                  unsubstantiable
                </>
              ) : null}
              .
            </p>

            {/*
              Failures first and in full. One unsupported claim blocks the whole piece, so
              burying it under twenty passing ones would hide the only thing that matters.
            */}
            {failing.length > 0 ? (
              <ul className="mt-5 flex flex-col gap-2.5">
                {failing.map((claim) => (
                  <li
                    key={`${claim.field}-${claim.kind}-${claim.text}`}
                    className="rounded-lg bg-[var(--surface-sunken)] p-3.5"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="pill pill-fail">
                        {claim.verdict === "banned" ? "Unsubstantiable" : "Unsupported"}
                      </span>
                      <span className="text-meta text-[var(--fg-quiet)]">
                        {humanise(claim.kind)}
                        {claim.field ? ` · ${humanise(claim.field)}` : ""}
                      </span>
                    </div>
                    <p className="mono mt-2 text-[var(--fg)]">{claim.text}</p>
                    <p className="mt-1.5 text-meta text-[var(--fg-secondary)]">
                      {claim.reason}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-4 flex gap-2 text-sm text-[var(--pass)]">
                <CheckIcon className="mt-0.5 shrink-0" />
                Every number, standard, material and regulated claim traces to a verified
                attribute.
              </p>
            )}

            <ClaimLedger claims={copy.claims.filter((c) => c.verdict === "supported")} />

            <p className="hairline-t mt-6 pt-5 text-meta text-[var(--fg-quiet)]">
              Checked arithmetically and by lookup, not by a second model. Quantities are
              compared after unit conversion; standards and alloy designations must appear
              verbatim in a verified value.
            </p>

            <FormalCheckPanel check={formal} />
          </Panel>
        </div>
      )}
    </section>
  );
}

/**
 * Validation layer L6, rendered beneath the claim check because it answers a different question.
 *
 * The claim check proves each statement *came from* a verified attribute. This proves the
 * statement is not *self-contradictory* given everything else the record establishes — a sentence
 * assembled entirely from real attributes can still assert something impossible, and only a solver
 * catches that.
 *
 * The three states are kept visibly distinct. "No contradiction found" and "the solver formed no
 * opinion" look similar in a summary and mean opposite things, so a passing-but-inconclusive
 * result never renders as verified.
 */
function FormalCheckPanel({ check }: { check: FormalCheck | null }) {
  // Not checked is its own state. Rendering a zeroed summary here would read as a clean result
  // from a layer that never ran.
  if (!check) {
    return (
      <div className="hairline-t mt-6 pt-5">
        <Overline>Formal verification</Overline>
        <p className="mt-2 text-meta text-[var(--fg-quiet)]">
          Not run for this SKU. Re-run the pipeline with{" "}
          <span className="mono">--verify-claims</span> to check the copy against the deployed
          Automated Reasoning policy.
        </p>
      </div>
    );
  }

  const contradictions = check.claims.filter((claim) => claim.contradiction);
  const indeterminate = check.claims.filter((claim) => claim.indeterminate);

  return (
    <div className="hairline-t mt-6 pt-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Overline>Formal verification · L6</Overline>
        <span
          className={clsx(
            "pill",
            check.error || contradictions.length > 0
              ? "pill-fail"
              : check.conclusive
                ? "pill-pass"
                : "pill-warn",
          )}
        >
          {check.error
            ? "Not verified"
            : contradictions.length > 0
              ? `${contradictions.length} contradiction${contradictions.length === 1 ? "" : "s"}`
              : check.conclusive
                ? "No contradiction"
                : "No verdict"}
        </span>
      </div>

      {check.error ? (
        // Fail-closed, and say so. An unreachable solver is not assent, and a reader has to know
        // the difference between "we checked and it was fine" and "we could not check".
        <p className="mt-2.5 text-meta text-[var(--fg-secondary)]">
          {check.error} Copy is withheld rather than published unverified.
        </p>
      ) : (
        <p className="mt-2.5 text-meta text-[var(--fg-secondary)]">
          <span className="tabular-nums text-[var(--fg)]">{check.checked}</span> sentence
          {check.checked === 1 ? "" : "s"} translated to logic and checked against the policy by
          an SMT solver
          {indeterminate.length > 0 ? (
            <>
              ;{" "}
              <span className="tabular-nums text-[var(--warn)]">{indeterminate.length}</span>{" "}
              could not be translated and {indeterminate.length === 1 ? "was" : "were"} left
              unverified
            </>
          ) : null}
          .
        </p>
      )}

      {/*
        A contradiction names the rule it violated. That identifier is the proof — it is what
        makes this a verdict rather than a score, so it is shown rather than summarised away.
      */}
      {contradictions.length > 0 ? (
        <ul className="mt-4 flex flex-col gap-2.5">
          {contradictions.map((claim) => (
            <li key={claim.claim} className="rounded-lg bg-[var(--surface-sunken)] p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="pill pill-fail">Proven contradiction</span>
                {claim.rules.map((rule) => (
                  <span key={rule} className="mono text-meta text-[var(--fg-quiet)]">
                    {rule}
                  </span>
                ))}
              </div>
              <p className="mt-2 text-sm text-[var(--fg)]">{claim.claim}</p>
              <p className="mt-1.5 text-meta text-[var(--fg-secondary)]">
                Contradicts the product&rsquo;s established facts. Remove the claim, or establish
                the attribute that would support it.
              </p>
            </li>
          ))}
        </ul>
      ) : null}

      {!check.error && !check.conclusive ? (
        <p className="mt-3 text-meta text-[var(--warn)]">
          The policy formed no opinion on any sentence, so nothing was disproven and nothing was
          verified. This is not a pass.
        </p>
      ) : null}

      {check.premises ? (
        <details className="group mt-4">
          <summary className="cursor-pointer text-meta text-[var(--fg-tertiary)] transition-colors duration-150 hover:text-[var(--fg)]">
            Premises the claims were judged against
          </summary>
          {/*
            Worth exposing: the premises are built from publishable values only, so a reader can
            confirm no queued value was smuggled in as an established fact.
          */}
          <p className="mt-2 text-meta leading-relaxed text-[var(--fg-secondary)]">
            {check.premises}
          </p>
        </details>
      ) : null}
    </div>
  );
}

/** Supported claims, collapsed. Present for audit, not for reading top to bottom. */
function ClaimLedger({ claims }: { claims: Claim[] }) {
  if (claims.length === 0) return null;

  // The same figure legitimately appears in several fields; the interesting unit is the claim
  // and what backs it, not each restatement.
  const unique = new Map<string, Claim>();
  for (const claim of claims) {
    unique.set(`${claim.kind}:${claim.text}:${claim.supported_by}`, claim);
  }

  return (
    <details className="group mt-6">
      <summary className="cursor-pointer text-sm text-[var(--fg-tertiary)] transition-colors duration-150 hover:text-[var(--fg)]">
        {unique.size} supported {unique.size === 1 ? "claim" : "claims"}, and what backs each
      </summary>
      <ul className="mt-3 flex flex-col gap-1.5">
        {[...unique.values()].map((claim) => (
          <li
            key={`${claim.kind}-${claim.text}-${claim.supported_by}`}
            className="grid grid-cols-[auto_1fr_auto] items-baseline gap-x-2.5 text-meta"
          >
            <span className="text-[var(--pass)]" aria-label="supported">
              <CheckIcon />
            </span>
            <span className="mono truncate text-[var(--fg-secondary)]">{claim.text}</span>
            <span className="mono text-[var(--fg-quiet)]">{claim.supported_by ?? "—"}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
