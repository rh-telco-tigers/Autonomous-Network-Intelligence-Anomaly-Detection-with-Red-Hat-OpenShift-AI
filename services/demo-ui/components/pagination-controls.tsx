"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

type PaginationControlsProps = {
  page: number;
  pageSize: number;
  totalItems: number;
  itemLabel: string;
  pageSizeOptions?: number[];
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
};

export function PaginationControls({
  page,
  pageSize,
  totalItems,
  itemLabel,
  pageSizeOptions = [5, 10, 20, 50],
  onPageChange,
  onPageSizeChange,
}: PaginationControlsProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(page, 1), totalPages);
  const start = totalItems ? (safePage - 1) * pageSize + 1 : 0;
  const end = totalItems ? Math.min(safePage * pageSize, totalItems) : 0;
  const [jumpPage, setJumpPage] = useState(String(safePage));
  const pageTokens = useMemo(() => buildPageTokens(safePage, totalPages), [safePage, totalPages]);

  useEffect(() => {
    setJumpPage(String(safePage));
  }, [safePage]);

  function submitJump() {
    const parsed = Number.parseInt(jumpPage.trim(), 10);
    if (!Number.isFinite(parsed)) {
      setJumpPage(String(safePage));
      return;
    }
    const targetPage = clampPage(parsed, totalPages);
    setJumpPage(String(targetPage));
    if (targetPage !== safePage) {
      onPageChange(targetPage);
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-4 xl:flex-row xl:items-center xl:justify-between">
      <div className="text-sm text-[var(--text-secondary)]">
        {totalItems ? `Showing ${start}-${end} of ${totalItems} ${itemLabel}` : `No ${itemLabel} to display`}
      </div>
      <div className="flex flex-col gap-3 xl:items-end">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            aria-label={`Rows per page for ${itemLabel}`}
            className="w-[110px]"
            value={String(pageSize)}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
          >
            {pageSizeOptions.map((option) => (
              <option key={option} value={option}>
                {option} / page
              </option>
            ))}
          </Select>
          <Button variant="secondary" size="sm" onClick={() => onPageChange(1)} disabled={safePage <= 1}>
            First
          </Button>
          <Button variant="secondary" size="sm" onClick={() => onPageChange(safePage - 1)} disabled={safePage <= 1}>
            Previous
          </Button>
          <div className="flex flex-wrap items-center gap-2">
            {pageTokens.map((token) =>
              typeof token === "number" ? (
                <Button
                  key={token}
                  variant={token === safePage ? "default" : "secondary"}
                  size="sm"
                  aria-current={token === safePage ? "page" : undefined}
                  onClick={() => onPageChange(token)}
                >
                  {token}
                </Button>
              ) : (
                <span key={token} className="px-1 text-sm text-[var(--text-muted)]">
                  ...
                </span>
              ),
            )}
          </div>
          <Button variant="secondary" size="sm" onClick={() => onPageChange(safePage + 1)} disabled={safePage >= totalPages}>
            Next
          </Button>
          <Button variant="secondary" size="sm" onClick={() => onPageChange(totalPages)} disabled={safePage >= totalPages}>
            Last
          </Button>
        </div>
        <form
          className="flex flex-wrap items-center gap-2 xl:justify-end"
          onSubmit={(event) => {
            event.preventDefault();
            submitJump();
          }}
        >
          <div className="text-sm text-[var(--text-secondary)]">
            Page {safePage} of {totalPages}
          </div>
          <Input
            type="number"
            inputMode="numeric"
            min={1}
            max={totalPages}
            className="h-9 w-24"
            aria-label={`Jump to page for ${itemLabel}`}
            value={jumpPage}
            onChange={(event) => setJumpPage(event.target.value)}
            onBlur={() => {
              const parsed = Number.parseInt(jumpPage.trim(), 10);
              setJumpPage(String(Number.isFinite(parsed) ? clampPage(parsed, totalPages) : safePage));
            }}
          />
          <Button type="submit" variant="secondary" size="sm">
            Go
          </Button>
        </form>
      </div>
    </div>
  );
}

function buildPageTokens(currentPage: number, totalPages: number) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const tokens: Array<number | string> = [1];
  let start = Math.max(2, currentPage - 2);
  let end = Math.min(totalPages - 1, currentPage + 2);

  while (end - start + 1 < 5) {
    if (start > 2) {
      start -= 1;
      continue;
    }
    if (end < totalPages - 1) {
      end += 1;
      continue;
    }
    break;
  }

  if (start > 2) {
    tokens.push("start-ellipsis");
  }

  for (let pageNumber = start; pageNumber <= end; pageNumber += 1) {
    tokens.push(pageNumber);
  }

  if (end < totalPages - 1) {
    tokens.push("end-ellipsis");
  }

  tokens.push(totalPages);
  return tokens;
}

function clampPage(page: number, totalPages: number) {
  return Math.max(1, Math.min(page, totalPages));
}
