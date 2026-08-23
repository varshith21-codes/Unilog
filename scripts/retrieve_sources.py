"""Find and fetch the manufacturer document for every row of an item master.

The step that closes ``no_source_available``. A six-column row carries a part number and a
forty-character description, and that is all there is to read — so the gap list for the client's
sample recommends ``retry_with_better_source`` on nearly two thousand attributes. This script goes
and gets the better source.

    # what would be fetched, and from where. No requests.
    python scripts/retrieve_sources.py "Unihack_ Sample Dataset - Input.csv" --dry-run

    # register documents you already have, then reuse them across every part they cover
    python scripts/retrieve_sources.py "Unihack_ Sample Dataset - Input.csv" `
      --add data/samples/ba100.txt --add data/samples/gv200.txt

    # which manufacturer domains are missing, ordered by how many rows they would unblock
    python scripts/retrieve_sources.py "Unihack_ Sample Dataset - Input.csv" --report-unresolved

    # supply a URL directly for one part
    python scripts/retrieve_sources.py "Unihack_ Sample Dataset - Input.csv" `
      --url "PDSH4816AF=https://www.frigidaire.com/en/p/PDSH4816AF"

Three rules, in the order they apply to each row:

1.  **Ask the library first.** A document already stored that covers this part means no request at
    all. This is where a thousand-row batch stops being a thousand fetches: one accessory catalogue
    answers for every part listed in it.
2.  **Prefer the manufacturer.** Resolution searches the manufacturer's own domain before the open
    web, and only a manufacturer-tier URL is citable as ``MFR URL``.
3.  **Refuse e-commerce.** Marketplaces, mass retail, distributors, datasheet aggregators and
    user-generated content are dropped *before* the request, so their bytes never enter the store
    and can never be cited by accident. The reasoning is in ``schema/sourcing.yaml`` beside each
    category.

**Reaching the open web needs a search provider, and there is no default.** Search is an external
service with a key and a bill, and wiring one in silently would make this script's behaviour depend
on ambient credentials. Point ``--search-module`` at your own callable — ``module:function`` taking
``(query, *, limit)`` and returning URLs — and the open arm turns on. Without it the script still
does useful work: it registers local documents, honours ``--url``, reuses everything in the library,
and reports exactly which manufacturer domains are missing.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

from axiom.delivery.batch import (  # noqa: E402
    InputColumnsError,
    select_rows,
    validate_input_columns,
)
from axiom.delivery.source import SupplierRow  # noqa: E402
from axiom.ingest import IngestError, LocalArtifactStore, ingest_file  # noqa: E402
from axiom.retrieve import (  # noqa: E402
    DocumentLibrary,
    FetchStatus,
    Resolver,
    RetrievalSession,
    SourcePolicy,
    SourceTier,
)
from axiom.retrieve.discover import SiteDiscovery  # noqa: E402
from axiom.retrieve.resolver import DISTRIBUTOR_ONLY  # noqa: E402

DEFAULT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"
DEFAULT_INDEX = REPO_ROOT / "data" / "library" / "index.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="supplier CSV (the Unilog item master)")
    parser.add_argument("--limit", type=int, help="process only the first N rows")
    parser.add_argument(
        "--mpn", action="append", default=[], help="process only these part numbers; repeatable"
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_INDEX,
        help=f"document library index (default: {DEFAULT_INDEX.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_STORE,
        help="content-addressed artifact store the documents live in",
    )
    parser.add_argument(
        "--add",
        type=Path,
        action="append",
        default=[],
        help=(
            "register a local document into the library before processing, e.g. a datasheet a "
            "supplier emailed. Repeatable. Its coverage is then discovered across every row."
        ),
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="MPN=URL",
        help="supply a URL for one part number; repeatable. Outranks every derived candidate.",
    )
    parser.add_argument(
        "--search-module",
        metavar="module:callable",
        help=(
            "a SearchProvider to reach the open web: a callable taking (query, *, limit) and "
            "returning URLs. Without one, only supplied URLs and declared patterns are tried."
        ),
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help=(
            "search the manufacturer's own site by reading its published search form, with no "
            "search API and no key. Tried when resolution produced no candidate, which without a "
            "--search-module is every row. Bounded: 4 pages per part, manufacturer domain only."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and plan but make no requests. Says what it would fetch and from where.",
    )
    parser.add_argument(
        "--max-fetches",
        type=int,
        default=50,
        help=(
            "hard ceiling on requests this run (default: 50). A deliberately low default: this "
            "reads sites that did not ask to be read, and an accidental thousand-request run is "
            "not something to discover afterwards."
        ),
    )
    parser.add_argument(
        "--report-unresolved",
        action="store_true",
        help=(
            "report which vendors have no declared manufacturer domain, ordered by the number of "
            "rows each would unblock, then exit. The work list for schema/sourcing.yaml."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the per-row log")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"no such file: {args.source}", file=sys.stderr)
        return 1

    rows = _read(args.source)
    if not rows:
        print(f"{args.source} contains no data rows", file=sys.stderr)
        return 1
    try:
        validate_input_columns(rows[0].keys())
    except InputColumnsError as exc:
        print(exc, file=sys.stderr)
        return 1

    policy = SourcePolicy.load()
    if not policy.manufacturers and not policy.excluded:
        print(
            "schema/sourcing.yaml declares no manufacturers and no exclusions. Nothing can be "
            "resolved and nothing would be refused; refusing to run rather than fetching blind.",
            file=sys.stderr,
        )
        return 1

    if args.report_unresolved:
        _report_unresolved(rows, policy)
        return 0

    supplied = _supplied_urls(args.url)
    if supplied is None:
        return 2

    search = _load_search_provider(args.search_module)
    if args.search_module and search is None:
        return 2

    store = LocalArtifactStore(args.store)
    library = DocumentLibrary.load(store, args.index)
    resolver = Resolver(policy, search=search)
    session = RetrievalSession(policy=policy, store=store, library=library)
    discovery = (
        SiteDiscovery(policy=policy, session=session, library=library)
        if args.discover and not args.dry_run
        else None
    )

    if not args.quiet:
        print(f"library     {len(library)} document(s) indexed at {_display(args.index)}")
        print(
            f"policy      {len(policy.manufacturers)} manufacturers, "
            f"{sum(len(g.domains) for g in policy.excluded)} excluded domains, "
            f"{'robots honoured' if policy.respect_robots_txt else 'ROBOTS IGNORED'}, "
            f"{policy.min_seconds_between_requests:g}s between requests"
        )
        print(
            f"search      {args.search_module if search else 'none configured (no open-web arm)'}"
        )
        print(
            f"discovery   {'the manufacturer site search form' if args.discover else 'off'}"
        )
        if args.dry_run:
            print("mode        DRY RUN: resolving only, no requests will be made")
            if args.discover:
                # Said out loud rather than silently ignored. Discovery *is* requests — it reads the
                # homepage to find the search form — so there is no dry-run version of it, and a
                # flag that appears to be honoured while doing nothing is worse than one refused.
                print(
                    "            --discover is inert here: it works by fetching, so it needs a "
                    "real run"
                )
        print()

    # --- documents handed to us, registered before anything is resolved ----------
    added = _register_local(args.add, store=store, library=library, quiet=args.quiet)

    selected = select_rows(rows, mpns=args.mpn, limit=args.limit)
    if not selected:
        print("nothing selected: --mpn/--limit matched no rows", file=sys.stderr)
        return 1

    stats: Counter[str] = Counter()
    fetch_status: Counter[str] = Counter()
    refused_categories: Counter[str] = Counter()
    unresolved_vendors: Counter[str] = Counter()
    discovery_notes: Counter[str] = Counter()
    fetches = 0

    for index, raw in enumerate(selected, start=1):
        row = SupplierRow.parse(dict(raw))
        if not row.identified:
            stats["no_part_number"] += 1
            continue
        mpn = row.mpn or ""

        # --- rule 1: the library first -----------------------------------------
        coverage = library.coverage_for(mpn)
        if coverage:
            best = coverage[0]
            stats["already_covered"] += 1
            if not args.quiet:
                print(
                    f"  {index:>5}  {mpn:24s} HAVE  {best.how:<5} "
                    f"{best.entry.tier:<12} {best.entry.document_id}"
                )
            continue

        # --- rule 2 and 3: resolve, under the policy ---------------------------
        resolution = resolver.resolve(
            mpn,
            brand=row.brand.brand,
            vendor_code=row.manufacturer.supplier_code,
            vendor_name=row.manufacturer.name,
            supplied=supplied.get(mpn, ()),
        )
        for verdict in resolution.rejected:
            refused_categories[verdict.category or "excluded"] += 1

        # --- arm 3: search the manufacturer's own site --------------------------
        # Only when resolution produced nothing, and only with a manufacturer to scope it to. This
        # is the arm that needs no API key, so without --search-module it is the only one that can
        # reach a document the library does not already have.
        if not resolution.candidates and discovery is not None and resolution.manufacturer:
            if fetches >= args.max_fetches:
                stats["over_budget"] += 1
            else:
                found = discovery.discover(mpn, resolution.manufacturer)
                fetches = session.requests_made
                if not args.quiet:
                    for step in found.steps:
                        print(
                            f"  {index:>5}  {mpn:24s} {step.stage[:5].upper():<5} "
                            f"{step.status:<14} {step.url[:52]}"
                        )
                if found.documents and library.coverage_for(mpn):
                    stats["discovered"] += 1
                    continue
                if found.documents:
                    stats["discovered_but_no_mention"] += 1
                    continue
                stats["discovery_found_nothing"] += 1
                for note in found.notes:
                    discovery_notes[note] += 1
                continue

        if not resolution.candidates:
            # Three different problems, and lumping them together is what made an earlier version of
            # this report misleading: it listed vendors under "unresolved" that resolve to a
            # manufacturer domain perfectly well and merely had no way to *search* it.
            if resolution.manufacturer is not None:
                stats["no_candidate_but_manufacturer_known"] += 1
            elif DISTRIBUTOR_ONLY in resolution.notes:
                stats["no_candidate_distributor_only"] += 1
            else:
                stats["no_candidate_no_manufacturer"] += 1
                label = row.manufacturer.name or row.brand.brand or "(nothing named)"
                unresolved_vendors[label] += 1
            if not args.quiet:
                reason = resolution.notes[0] if resolution.notes else "no candidates"
                print(f"  {index:>5}  {mpn:24s} NONE  {reason[:64]}")
            continue

        stats["resolved"] += 1
        if args.dry_run:
            for candidate in resolution.candidates:
                if not args.quiet:
                    print(
                        f"  {index:>5}  {mpn:24s} PLAN  {candidate.tier.value:<12} "
                        f"{candidate.origin:<12} {candidate.url[:64]}"
                    )
            continue

        # --- fetch, best candidate first, stopping at the first usable document -
        for candidate in resolution.candidates:
            if fetches >= args.max_fetches:
                stats["over_budget"] += 1
                break
            outcome = session.fetch(candidate)
            fetch_status[outcome.status.value] += 1
            if outcome.status is FetchStatus.FETCHED:
                fetches += 1
            if outcome.status is FetchStatus.REFUSED_POLICY:
                refused_categories[candidate.verdict.category or "excluded"] += 1

            if not args.quiet:
                print(
                    f"  {index:>5}  {mpn:24s} {outcome.status.value.upper()[:5]:<5} "
                    f"{candidate.tier.value:<12} {candidate.url[:56]}"
                )
            if not outcome.usable:
                continue

            # Does the document we just fetched actually cover the part we fetched it for? A
            # landing page that never names the part number is not a source for it, and storing it
            # as one would put an unrelated document behind a citation.
            if library.coverage_for(mpn):
                stats["fetched_and_covers"] += 1
                break
            stats["fetched_but_no_mention"] += 1

    index_path = None
    if not args.dry_run:
        index_path = library.save()

    _report(
        args,
        library=library,
        session=session,
        stats=stats,
        fetch_status=fetch_status,
        refused=refused_categories,
        unresolved_vendors=unresolved_vendors,
        discovery_notes=discovery_notes,
        added=added,
        index_path=index_path,
        selected=len(selected),
    )
    return 0


# ------------------------------------------------------------------------------- helpers


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _supplied_urls(pairs: list[str]) -> dict[str, tuple[str, ...]] | None:
    """Parse ``--url MPN=URL`` into a mapping, or report the malformed one and return None."""
    out: dict[str, list[str]] = {}
    for pair in pairs:
        mpn, _, url = pair.partition("=")
        if not mpn.strip() or not url.strip():
            print(
                f"--url expects MPN=URL, got {pair!r}. The part number comes first so the URL may "
                f"contain '=' in its query string.",
                file=sys.stderr,
            )
            return None
        out.setdefault(mpn.strip(), []).append(url.strip())
    return {mpn: tuple(urls) for mpn, urls in out.items()}


def _load_search_provider(spec: str | None):
    """Import ``module:callable``, or explain why it could not be used."""
    if not spec:
        return None
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        print(f"--search-module expects module:callable, got {spec!r}", file=sys.stderr)
        return None
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        print(f"could not import {module_name!r}: {exc}", file=sys.stderr)
        return None
    provider = getattr(module, attr, None)
    if provider is None or not callable(provider):
        print(f"{spec!r} is not a callable", file=sys.stderr)
        return None
    return provider


def _register_local(
    paths: list[Path], *, store: LocalArtifactStore, library: DocumentLibrary, quiet: bool
) -> int:
    """Ingest local documents into the store and index them.

    Coverage is not computed here — it is discovered per row later, by the same ``coverage_for``
    call a retrieved document goes through, so a hand-supplied datasheet and a fetched one are
    treated identically.
    """
    added = 0
    for path in paths:
        if not path.is_file():
            print(f"no such document: {path}", file=sys.stderr)
            continue
        try:
            artifact = ingest_file(path, store)
        except IngestError as exc:
            print(f"could not ingest {path}: {exc}", file=sys.stderr)
            continue
        entry = library.register(
            artifact,
            # A local file has no host and no tier to claim. `unknown` is the honest label: it is a
            # real document, and nothing here establishes that it is the manufacturer's own.
            host="",
            tier=SourceTier.UNKNOWN.value,
        )
        added += 1
        if not quiet:
            print(f"  added   {entry.document_id}  {_display(path)}  ({entry.size_bytes:,} bytes)")
    if added and not quiet:
        print()
    return added


def _report_unresolved(rows: list[dict[str, str]], policy: SourcePolicy) -> None:
    """Which vendors have no domain, ordered by how many rows a domain would unblock.

    The point of the ordering: adding one line to ``sourcing.yaml`` for the vendor at the top is
    worth more than the whole tail put together, and a flat alphabetical list hides that.
    """
    missing: Counter[tuple[str, str]] = Counter()
    distributor_rows = 0
    resolved_rows = 0
    unnamed = 0

    for raw in rows:
        row = SupplierRow.parse(dict(raw))
        if not row.identified:
            continue
        maker = policy.manufacturer_for(
            vendor_code=row.manufacturer.supplier_code,
            vendor_name=row.manufacturer.name,
            brand=row.brand.brand,
        )
        if maker:
            resolved_rows += 1
            continue
        if policy.is_known_distributor(
            vendor_code=row.manufacturer.supplier_code, vendor_name=row.manufacturer.name
        ):
            distributor_rows += 1
            continue
        name = row.manufacturer.name or ""
        code = row.manufacturer.supplier_code or ""
        if not name and not row.brand.resolved:
            unnamed += 1
            continue
        missing[(code, name or f"(brand only: {row.brand.brand})")] += 1

    total = sum(1 for r in rows if SupplierRow.parse(dict(r)).identified)
    print("=" * 78)
    print("MANUFACTURER RESOLUTION COVERAGE")
    print("=" * 78)
    print(f"  rows with an identified part number      {total:5d}")
    print(f"  resolve to a declared manufacturer       {resolved_rows:5d}")
    print(f"  named party is a declared distributor    {distributor_rows:5d}")
    print(f"  nothing named at all                     {unnamed:5d}")
    print(f"  vendor known but no domain declared      {sum(missing.values()):5d}")

    if distributor_rows:
        print(
            f"\n  The {distributor_rows} distributor rows are not a gap this file can close. A "
            f"distributor has no\n  datasheet; the manufacturer has to come from the brand column, "
            f"and on these rows it is a\n  sentinel. Those need the brand master "
            f"(UniCat_Manufacturer_and_Brand_List.xlsx)."
        )

    if not missing:
        print("\n  Every named manufacturer has a declared domain.")
        return

    print(f"\n  {'rows':>5}  {'code':<8} vendor  (add a `domains:` entry for these)")
    print("  " + "-" * 74)
    for (code, name), count in missing.most_common():
        print(f"  {count:>5}  {code:<8} {name}")


def _display(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _report(
    args,
    *,
    library: DocumentLibrary,
    session: RetrievalSession,
    stats: Counter,
    fetch_status: Counter,
    refused: Counter,
    unresolved_vendors: Counter,
    discovery_notes: Counter,
    added: int,
    index_path: Path | None,
    selected: int,
) -> None:
    print()
    print("=" * 78)
    print(f"rows processed   {selected}")
    if added:
        print(f"documents added  {added} from --add")
    print()
    print(f"  already covered by the library   {stats['already_covered']:5d}   no request needed")
    print(f"  resolved to a candidate          {stats['resolved']:5d}")

    known = stats["no_candidate_but_manufacturer_known"]
    if known:
        print(
            f"  manufacturer known, no candidate {known:5d}   "
            f"needs --search-module or a declared pattern"
        )
    if stats["no_candidate_distributor_only"]:
        print(
            f"  only a distributor is named      {stats['no_candidate_distributor_only']:5d}   "
            f"no datasheet exists to fetch"
        )
    if stats["no_candidate_no_manufacturer"]:
        print(
            f"  no manufacturer established      {stats['no_candidate_no_manufacturer']:5d}   "
            f"see --report-unresolved"
        )

    discovered = stats["discovered"]
    if discovered or stats["discovery_found_nothing"] or stats["discovered_but_no_mention"]:
        print("\n  site discovery (the manufacturer's own search form):")
        print(f"    found a covering document      {discovered:5d}")
        if stats["discovered_but_no_mention"]:
            print(
                f"    fetched but part not named     {stats['discovered_but_no_mention']:5d}   "
                f"stored, but not a source for that part"
            )
        print(f"    found nothing                  {stats['discovery_found_nothing']:5d}")
        for note, count in discovery_notes.most_common(4):
            print(f"      {count:>4}x {note[:88]}")
    if stats["no_part_number"]:
        print(f"  skipped, no part number          {stats['no_part_number']:5d}")
    if stats["over_budget"]:
        print(
            f"  left unfetched, over budget      {stats['over_budget']:5d}   "
            f"raise --max-fetches to continue"
        )

    if fetch_status:
        print("\n  fetch outcomes:")
        for status, count in fetch_status.most_common():
            print(f"    {status:<18} {count:5d}")
        print(f"    {'requests made':<18} {session.requests_made:5d}")
        print(f"    {'bytes fetched':<18} {session.bytes_fetched:5d}")

    if stats["fetched_and_covers"] or stats["fetched_but_no_mention"]:
        print(
            f"\n  of the documents fetched, {stats['fetched_and_covers']} named the part they were "
            f"fetched for and\n  {stats['fetched_but_no_mention']} did not. The second group is "
            f"stored but is not a source for that\n  part: a page that never names the part number "
            f"cannot be cited for it."
        )

    if refused:
        print("\n  refused before any request was made:")
        for category, count in refused.most_common():
            print(f"    {category:<24} {count:5d}")
        print(
            "    These are marketplaces, retailers, distributors and aggregators. Their bytes\n"
            "    never entered the store, so nothing can cite them by accident."
        )

    if known and not args.dry_run:
        print(
            f"\n  {known} row(s) named a manufacturer whose domain is declared and still "
            f"produced no\n  candidate. That is not a data gap: it is the missing search arm. "
            f"Either point\n  --search-module at a provider, or add a verified `patterns:` entry "
            f"for the\n  manufacturer in schema/sourcing.yaml."
        )

    if unresolved_vendors:
        print("\n  vendors with no declared manufacturer domain (rows that would unblock):")
        for name, count in unresolved_vendors.most_common(10):
            print(f"    {count:>5}  {name}")
        print("    Run again with --report-unresolved for the full work list.")

    print("\n  library now:")
    for key, value in library.stats().items():
        print(f"    {key:<24} {value}")
    if index_path:
        print(f"\n  index written to {_display(index_path)}")

    print("\n  next: run the catalogue exporter, which reads this library and extracts from")
    print("  whatever it covers:")
    print('    python scripts/export_console_catalogue.py "<input.csv>" --risk-budget 0.05')


if __name__ == "__main__":
    raise SystemExit(main())
