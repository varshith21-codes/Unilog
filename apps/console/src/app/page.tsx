import type { Metadata } from "next";
import Link from "next/link";

import { MarketingHeader } from "@/components/marketing-header";
import { ArrowIcon } from "@/components/primitives";
import { ProductMark } from "@/components/product-mark";

export const metadata: Metadata = {
  title: "Verifiable Product Intelligence",
  description:
    "AXIOM turns six-field inputs into evidence-backed, reviewable, delivery-ready records without guessing.",
};

const workflow = [
  { index: "01", name: "Retrieve", detail: "Manufacturer PDF" },
  { index: "02", name: "Verify", detail: "Exact quote matched" },
  { index: "03", name: "Review", detail: "Not required" },
  { index: "04", name: "Publish", detail: "Eligible" },
];

export default function LandingPage() {
  return (
    <div className="marketing-page">
      <MarketingHeader />

      <main id="main">
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="marketing-container">
            <div className="landing-hero-grid">
              <div className="landing-hero-heading hero-entrance hero-entrance-1">
                <p className="landing-eyebrow">
                  Verifiable Product Intelligence for Industrial Commerce
                </p>
                <h1 id="landing-title" className="landing-title">
                  From six supplier fields to a governed 252-column record.
                </h1>
              </div>

              <aside
                className="hero-trace hero-entrance hero-entrance-2"
                aria-labelledby="hero-trace-title"
              >
                <header className="hero-trace-header">
                  <div>
                    <p id="hero-trace-title">Compilation trace</p>
                    <p className="hero-trace-record">BA-100-025</p>
                  </div>
                  <span>Evidence gated</span>
                </header>

                <ol className="hero-trace-list">
                  <li>
                    <span className="hero-trace-index">01</span>
                    <div>
                      <small>Input</small>
                      <strong>6 supplier fields</strong>
                      <p>Sparse supplier record</p>
                    </div>
                  </li>
                  <li>
                    <span className="hero-trace-index">02</span>
                    <div>
                      <small>Exact verified span</small>
                      <strong>Bronze C84400</strong>
                      <p>Manufacturer PDF · page 1</p>
                    </div>
                  </li>
                  <li>
                    <span className="hero-trace-index">03</span>
                    <div>
                      <small>Governed result</small>
                      <strong className="hero-trace-pass">Auto-accepted</strong>
                      <p>Confidence 0.90 · evidence verified</p>
                    </div>
                  </li>
                  <li>
                    <span className="hero-trace-index">04</span>
                    <div>
                      <small>Contract output</small>
                      <strong>252-column UniLog record</strong>
                      <p>Provenance recorded per cell</p>
                    </div>
                  </li>
                </ol>

                <p className="hero-trace-rule">
                  <span>Publication rule</span>
                  No independent evidence → no automatic publication.
                </p>
              </aside>

              <div className="landing-hero-support hero-entrance hero-entrance-3">
                <p className="landing-intro">
                  AXIOM retrieves source evidence, verifies each value, and records
                  provenance for every populated or withheld UniLog cell—without guessing.
                </p>
                <div className="landing-actions">
                  <Link href="/enrich" className="btn btn-primary">
                    Enrich a product record
                    <ArrowIcon />
                  </Link>
                  <Link href="#evidence-chain" className="landing-text-link">
                    See the evidence chain
                    <span aria-hidden="true">↓</span>
                  </Link>
                </div>
              </div>
            </div>

            <dl
              className="landing-outcome-rail hero-entrance hero-entrance-4"
              aria-label="Compilation outcomes"
            >
              <div>
                <dt>Input</dt>
                <dd>
                  <strong>6 fields in</strong>
                  <span>Sparse supplier record</span>
                </dd>
              </div>
              <div>
                <dt>Evidence</dt>
                <dd>
                  <strong>Independently verified</strong>
                  <span>Exact source spans retained</span>
                </dd>
              </div>
              <div>
                <dt>Delivery</dt>
                <dd>
                  <strong>252-column contract</strong>
                  <span>Provenance for populated and withheld cells</span>
                </dd>
              </div>
            </dl>
          </div>
        </section>

        <section
          id="evidence-chain"
          className="landing-proof-section"
          aria-labelledby="proof-title"
        >
          <div className="marketing-container">
            <div className="landing-section-heading">
              <h2 id="proof-title">One value. Every decision attached.</h2>
              <p>
                Follow BA-100-025 from its exact source span through normalization,
                validation, publication policy, and UniLog delivery eligibility.
              </p>
            </div>

            <article className="evidence-console" aria-label="Field-level evidence record">
              <header className="evidence-console-header">
                <p className="evidence-record-id">
                  <span>Console fixture</span>
                  BA-100-025
                  <span>Class</span>
                  PLB.VLV.BALL.2PC
                </p>
                <p className="evidence-output-target">
                  <span>Output</span>
                  UniLog contract
                </p>
              </header>

              <div className="evidence-console-grid">
                <div className="evidence-governance">
                  <p className="evidence-attribute-code">Field / body_material</p>
                  <p className="evidence-attribute-name">Body material</p>
                  <p className="evidence-value">Bronze C84400</p>

                  <div className="evidence-decision">
                    <strong>Publishable · Auto-accepted</strong>
                    <span>Decision score 0.728 / policy gate 0.707</span>
                  </div>

                  <dl className="evidence-metadata">
                    <div>
                      <dt>Confidence</dt>
                      <dd>0.90</dd>
                    </div>
                    <div>
                      <dt>Evidence</dt>
                      <dd className="evidence-pass">Verified</dd>
                    </div>
                    <div>
                      <dt>Derivation</dt>
                      <dd>Document extraction</dd>
                    </div>
                    <div>
                      <dt>Locator</dt>
                      <dd>PDF · page 1</dd>
                    </div>
                  </dl>
                </div>

                <div className="evidence-source">
                  <div className="evidence-document">
                    <header className="evidence-document-header">
                      <span>Manufacturer specification</span>
                      <span>PDF · page 1</span>
                    </header>
                    <div className="evidence-document-page">
                      <p className="evidence-document-kicker">Milwaukee Valve</p>
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
                        <span>600 PSI WOG @ 73°F</span>
                      </p>
                      <p className="evidence-document-line">
                        <span>End Connection</span>
                        <span>NPT threaded</span>
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <ol className="evidence-flow" aria-label="Retrieve, verify, review, and publish workflow">
                {workflow.map((stage) => (
                  <li key={stage.index}>
                    <span className="evidence-flow-index">{stage.index}</span>
                    <strong>{stage.name}</strong>
                    <small>{stage.detail}</small>
                  </li>
                ))}
              </ol>
            </article>
          </div>
        </section>

        <section className="landing-closing" aria-labelledby="closing-title">
          <div className="marketing-container landing-closing-inner">
            <div>
              <h2 id="closing-title">Compile only what the evidence can support.</h2>
              <p>
                Enter the workspace to retrieve, verify, review, and publish governed
                product records.
              </p>
            </div>
            <div className="landing-actions">
              <Link href="/operations" className="btn btn-primary">
                Open operations workspace
                <ArrowIcon />
              </Link>
            </div>
          </div>
        </section>
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
