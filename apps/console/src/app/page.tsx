import type { Metadata } from "next";
import Link from "next/link";

import {
  AdversarialBand,
  ClosingSection,
  ContractSection,
  CoverageSection,
  EconomicsSection,
  EvidenceSection,
  MeasuredSection,
  PipelineSection,
  PrinciplesSection,
  SourcingSection,
  ValidationSection,
} from "@/components/landing-sections";
import { FigureBand } from "@/components/landing-primitives";
import { MarketingHeader } from "@/components/marketing-header";
import { ArrowIcon } from "@/components/primitives";
import { ProductMark } from "@/components/product-mark";
import {
  CORPUS,
  HEADLINE_FIGURES,
  POLICY,
  RECORD,
  SECTIONS,
  TRACE,
} from "@/data/landing";

export const metadata: Metadata = {
  title: "Verifiable Product Intelligence",
  description:
    "AXIOM turns six supplier fields into an evidence-backed, reviewable, delivery-ready " +
    "252-column record — and records provenance for every cell it withholds.",
};

/**
 * The public landing page.
 *
 * Direction: Industrial Precision, composed at the density of a technical specification sheet.
 * Warm paper canvas, cool graphite structure, one cobalt accent, hairline hierarchy, no
 * gradients. Depth comes from surface steps and rules rather than from shadow.
 *
 * The page argues a single claim — that a product record can be compiled without guessing — and
 * every section exists to make that claim checkable rather than to restate it. So the density is
 * the design: 252 columns, 13 stages, 7 validation layers, 67 rules, 32 classes, 64 adversarial
 * requests, 1,007 recorded runs, and a numbered source for each. A page selling verifiability
 * that asks to be taken on faith would be arguing against itself.
 *
 * Composition rules held throughout:
 *
 * - **No two adjacent sections share a shape.** Hero, band, console, ladder, table, mosaic,
 *   bento, triptych, full-bleed band, tag field. Repeated three-column card rows are the most
 *   common tell of a generated page.
 * - **Every figure carries its denominator.** "0 fabricated" is meaningless until it is 0 of 64.
 * - **The unflattering numbers stay.** Offline coverage of 50.7%, one wrong cell, an untrained
 *   calibrator, 3,377 gaps against 2,156 values.
 * - **No JavaScript for layout or motion.** Entrance stagger and scroll reveals are CSS, guarded
 *   by `@supports` and `prefers-reduced-motion`, so the first paint never waits on hydration.
 */
export default function LandingPage() {
  return (
    <div className="marketing-page">
      <MarketingHeader sections={SECTIONS} />

      <main id="main">
        {/* ------------------------------------------------------------ hero */}
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="marketing-container">
            <div className="landing-hero-grid">
              <div className="landing-hero-heading hero-entrance hero-entrance-1">
                <p className="landing-eyebrow">
                  Verifiable product intelligence for industrial commerce
                </p>
                <h1 id="landing-title" className="landing-title">
                  Product specifications that can prove where they came from.
                </h1>
              </div>

              <div className="landing-hero-support hero-entrance hero-entrance-3">
                <p className="landing-intro">
                  AXIOM takes six supplier fields, retrieves the manufacturer's own document, and
                  verifies every value against an exact quote inside it. Each of the 252 delivery
                  cells then carries either its evidence or the recorded reason it was withheld.
                  No evidence produces a typed gap, never a guess.
                </p>
                <div className="landing-actions">
                  <Link href="/enrich" className="btn btn-primary">
                    Enrich a product record
                    <ArrowIcon />
                  </Link>
                  <Link href="#evidence" className="landing-text-link">
                    Read the evidence chain
                    <span aria-hidden="true">↓</span>
                  </Link>
                </div>
              </div>

              {/*
                The hero figure. A trace of one real record rather than an illustration, which is
                why it names the SKU, the document hash and the threshold: everything in it can be
                checked against `data/console/BA-100-075.bundle.json`.
              */}
              <aside
                className="hero-trace hero-entrance hero-entrance-2"
                aria-labelledby="hero-trace-title"
              >
                <header className="hero-trace-header">
                  <div>
                    <p id="hero-trace-title">Compilation trace</p>
                    <p className="hero-trace-record">{RECORD.sku}</p>
                  </div>
                  <span>Evidence gated</span>
                </header>

                <ol className="hero-trace-list">
                  {TRACE.map((step) => (
                    <li
                      key={step.index}
                      data-highlight={"highlight" in step && step.highlight ? true : undefined}
                    >
                      <span className="hero-trace-index">{step.index}</span>
                      <div>
                        <small>{step.label}</small>
                        <strong
                          className={
                            "tone" in step && step.tone === "pass" ? "hero-trace-pass" : undefined
                          }
                        >
                          {step.value}
                        </strong>
                        <p>{step.detail}</p>
                      </div>
                    </li>
                  ))}
                </ol>

                <p className="hero-trace-rule">
                  <span>Publication rule</span>
                  No independent evidence → no automatic publication.
                </p>
              </aside>
            </div>

            <div className="landing-hero-band hero-entrance hero-entrance-4">
              <FigureBand
                label="Headline figures"
                columns={3}
                items={HEADLINE_FIGURES.map((figure) => ({ ...figure }))}
              />
              <p className="landing-hero-band-note">
                Aggregates describe {CORPUS.records} recorded runs read from checked-in bundles,
                not live traffic. The acceptance threshold shown throughout is {POLICY.threshold},
                produced by a held-out calibration set at a {POLICY.epsilon} error budget and{" "}
                {POLICY.confidenceLevel} confidence.
              </p>
            </div>
          </div>
        </section>

        {/* --------------------------------------------------------- content */}
        <EvidenceSection />
        <PipelineSection />
        <ValidationSection />
        <ContractSection />
        <MeasuredSection />
        <AdversarialBand />
        <SourcingSection />
        <CoverageSection />
        <EconomicsSection />
        <PrinciplesSection />
        <ClosingSection />
      </main>

      <footer className="marketing-footer">
        <div className="marketing-container marketing-footer-inner">
          <div className="marketing-footer-signature">
            <ProductMark />
            <span>AXIOM / Evidence over inference</span>
          </div>
          <p className="marketing-footer-rule">
            No independent evidence → no automatic publication.
          </p>
        </div>
      </footer>
    </div>
  );
}
