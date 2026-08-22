/**
 * Server-side pagination for the catalogue list views.
 *
 * These pages were written against a two-SKU catalogue and rendered every record they were given.
 * That is the right shape for two and the wrong one for a thousand: a full item master turns the
 * resolve queue into several megabytes of HTML per request, and no reviewer scrolls past the first
 * screen of a risk-ordered list anyway.
 *
 * Truncation was the cheaper fix and it is the dishonest one — a capped list is indistinguishable
 * from a short list, so a record on page four simply ceases to exist as far as the UI is concerned.
 * Paging keeps every record reachable and states the total.
 *
 * Deliberately a pure function over an already-loaded array rather than a range request to the API.
 * The dataset endpoint returns the whole catalogue in one document and the console memoises it per
 * request, so slicing here costs nothing and keeps the seam where it is. If the catalogue outgrows
 * one response, this is the call site to change, and the page components will not need to move.
 */

export interface Page<T> {
  items: T[];
  /** 1-based, clamped into range. An out-of-range `?page=` shows the last page, not an empty one. */
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
  /** Index of the first item on this page, 1-based, for "showing 51-100 of 1001". */
  from: number;
  /** Index of the last item on this page, 1-based and inclusive. */
  to: number;
  hasPrevious: boolean;
  hasNext: boolean;
}

export const DEFAULT_PAGE_SIZE = 50;

/**
 * Parse a `?page=` value.
 *
 * Anything unparseable is page 1 rather than an error. A malformed query string is not worth a 400
 * on a read-only list, and a reviewer who hand-edits a URL should land somewhere sensible.
 */
export function pageParam(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = Number.parseInt(raw ?? "1", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

export function paginate<T>(
  items: T[],
  requested: number,
  pageSize: number = DEFAULT_PAGE_SIZE,
): Page<T> {
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Math.max(1, Math.trunc(requested)), pageCount);
  const start = (page - 1) * pageSize;
  const slice = items.slice(start, start + pageSize);

  return {
    items: slice,
    page,
    pageCount,
    pageSize,
    total,
    // Zero when there is nothing to show, so a caller rendering "showing 1-0 of 0" has the
    // information to render an empty state instead.
    from: total === 0 ? 0 : start + 1,
    to: start + slice.length,
    hasPrevious: page > 1,
    hasNext: page < pageCount,
  };
}
