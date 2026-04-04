import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTime(value: string | null | undefined) {
  if (!value) {
    return "Unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function formatRelativeNumber(value: number | null | undefined, digits = 2) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return "0";
  }
  return numeric.toFixed(digits);
}

export function formatInteger(value: number | null | undefined) {
  return new Intl.NumberFormat().format(Math.round(Number(value ?? 0)));
}

export function titleize(value: string | null | undefined) {
  return String(value ?? "unknown")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function isExternalUrl(value: string | null | undefined) {
  return /^https?:\/\//.test(String(value ?? ""));
}
