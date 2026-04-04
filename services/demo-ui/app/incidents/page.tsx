import { IncidentsPageClient } from "@/components/pages/incidents-page-client";

export default async function IncidentsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; severity?: string; q?: string }>;
}) {
  const resolvedSearchParams = await searchParams;
  return <IncidentsPageClient initialFilters={resolvedSearchParams} />;
}
