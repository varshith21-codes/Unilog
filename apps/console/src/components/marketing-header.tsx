import Link from "next/link";

import { ProductMark } from "@/components/product-mark";
import { ThemeToggle } from "@/components/theme-toggle";

export function MarketingHeader() {
  return (
    <header className="marketing-header">
      <div className="marketing-container marketing-header-inner">
        <Link href="/" className="marketing-brand" aria-label="AXIOM home">
          <ProductMark />
          <span className="marketing-brand-copy">
            <strong>AXIOM</strong>
            <small>Product intelligence</small>
          </span>
        </Link>

        <div className="marketing-header-actions">
          <div className="marketing-theme-control">
            <ThemeToggle />
          </div>
          <Link href="/operations" className="btn btn-quiet">
            Enter operations
          </Link>
        </div>
      </div>
    </header>
  );
}
