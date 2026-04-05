"use client";

import { Button } from "@/components/ui/button";
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

  return (
    <div className="flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-4 md:flex-row md:items-center md:justify-between">
      <div className="text-sm text-[var(--text-secondary)]">
        {totalItems ? `Showing ${start}-${end} of ${totalItems} ${itemLabel}` : `No ${itemLabel} to display`}
      </div>
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
        <Button variant="secondary" size="sm" onClick={() => onPageChange(safePage - 1)} disabled={safePage <= 1}>
          Previous
        </Button>
        <div className="min-w-[72px] text-center text-sm text-[var(--text-secondary)]">
          Page {safePage} / {totalPages}
        </div>
        <Button variant="secondary" size="sm" onClick={() => onPageChange(safePage + 1)} disabled={safePage >= totalPages}>
          Next
        </Button>
      </div>
    </div>
  );
}
