import type { TicketRecord } from "@/lib/types";

function directTicketUrl(url: string | null | undefined): string {
  const value = String(url ?? "").trim();
  if (!value) {
    return "";
  }
  if (value.startsWith("/")) {
    return value;
  }
  try {
    const parsed = new URL(value);
    if (parsed.host === "plane.demo.local") {
      return "";
    }
    return value;
  } catch {
    return "";
  }
}

export function resolveTicketHref(ticket?: Pick<TicketRecord, "provider" | "external_id" | "url"> | null): string {
  const directUrl = directTicketUrl(ticket?.url);
  if (directUrl) {
    return directUrl;
  }
  const provider = String(ticket?.provider ?? "").trim();
  const externalId = String(ticket?.external_id ?? "").trim();
  if (!provider || !externalId) {
    return "";
  }
  return `/tickets/${encodeURIComponent(provider)}/${encodeURIComponent(externalId)}`;
}

export function resolveDirectTicketUrl(ticket?: Pick<TicketRecord, "url"> | null): string {
  return directTicketUrl(ticket?.url);
}
