import Link from "next/link";

import { MarketingHeader } from "@/components/marketing-header";
import { ArrowIcon } from "@/components/primitives";

export const metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <div className="marketing-page">
      <MarketingHeader />
      <main id="main" className="landing-closing">
        <div className="marketing-container landing-closing-inner">
          <div>
            <p className="landing-eyebrow">404 / Route unavailable</p>
            <h1 className="landing-title">This record path does not exist.</h1>
            <p className="landing-intro">
              Return to the public overview or continue into catalog operations.
            </p>
          </div>
          <div className="landing-actions">
            <Link href="/" className="btn btn-quiet">
              AXIOM overview
            </Link>
            <Link href="/operations" className="btn btn-primary">
              Enter operations
              <ArrowIcon />
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
