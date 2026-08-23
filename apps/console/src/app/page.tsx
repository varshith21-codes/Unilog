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
          <div className="marketing-container landing-hero-grid">
            <div>
              <p className="landing-eyebrow">
                Verifiable Product Intelligence for Industrial Commerce
              </p>
              <h1 id="landing-title" className="landing-title">
                From sparse product data to trusted catalog intelligence.
              </h1>
              <p className="landing-intro">
                AXIOM turns six-field inputs into evidence-backed, reviewable,
                delivery-ready records—without guessing.
              </p>
              <div className="landing-actions">
                <Link href="/operations" className="btn btn-primary">
                  Enter operations
                  <ArrowIcon />
                </Link>
                <Link href="/enrich" className="btn btn-quiet">
                  Enrich a record
                </Link>
              </div>
            </div>

            <aside className="landing-category" aria-label="Product definition and trust rule">
              <p className="landing-category-label">Product category</p>
              <strong>An evidence-gated compiler for product data.</strong>
              <p className="landing-rule">
                <span>Publication rule</span>
                No independent evidence means no automatic publication.
              </p>
            </aside>
          </div>
        </section>

        <section className="landing-proof-section" aria-labelledby="proof-title">
          <div className="marketing-container">
            <div className="landing-section-heading">
              <h2 id="proof-title">Every published value arrives with its proof.</h2>
              <p>
                AXIOM binds a normalized value to its exact source span, confidence,
                validation state, and publication decision before delivery.
              </p>
            </div>

            <article className="evidence-console reveal reveal-1" aria-label="Field-level evidence record">
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

            <div className="landing-proof-line" aria-label="Delivery proof">
              <div>
                <strong>252 columns</strong>
                <span>Exact UniLog delivery contract</span>
              </div>
              <div>
                <strong>Per cell</strong>
                <span>Provenance for populated and withheld output</span>
              </div>
            </div>
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
                Open AXIOM
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
