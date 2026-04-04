"use client";

import * as React from "react";
import { ExternalLink } from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConsoleStateQuery } from "@/lib/api";
import { formatInteger, isExternalUrl, titleize } from "@/lib/utils";

type IntegrationStatus = Record<string, unknown>;

function buildRouteLinks(origin: string, integrations: Record<string, IntegrationStatus>) {
  const replaceHost = (from: string, to: string) => (origin.includes(from) ? origin.replace(from, to) : "");
  const planeIntegration = integrations.plane ?? {};
  const planeRoute = String(planeIntegration.app_url ?? process.env.NEXT_PUBLIC_PLANE_URL ?? "").trim();
  return [
    { label: "Grafana", url: process.env.NEXT_PUBLIC_GRAFANA_URL ?? replaceHost("demo-ui", "grafana") },
    { label: "Control Plane", url: process.env.NEXT_PUBLIC_CONTROL_PLANE_URL ?? replaceHost("demo-ui", "control-plane") },
    { label: "Feature Gateway", url: replaceHost("demo-ui", "feature-gateway") },
    { label: "Milvus Attu", url: process.env.NEXT_PUBLIC_ATTU_URL ?? replaceHost("demo-ui", "milvus-attu") },
    {
      label: "Plane",
      url: Boolean(planeIntegration.live_configured) && isExternalUrl(planeRoute) ? planeRoute : "",
    },
  ].filter((item) => item.url);
}

export default function ServicesPage() {
  const { data, isLoading, error } = useConsoleStateQuery(10_000);
  const [origin, setOrigin] = React.useState("");

  React.useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  if (isLoading) {
    return <div className="text-sm text-slate-400">Loading services...</div>;
  }
  if (error || !data) {
    return <div className="text-sm text-rose-300">Could not load service data.</div>;
  }

  const routeLinks = buildRouteLinks(origin, data.integrations);
  const configuredLinks = routeLinks.filter((item) => isExternalUrl(item.url));

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Platform connectivity"
        title="Services"
        description="Service health, integration readiness, and direct navigation links when routes are exposed."
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <ServiceMetric label="Healthy services" value={formatInteger(data.summary.healthy_services)} />
        <ServiceMetric label="Total services" value={formatInteger(data.summary.service_count)} />
        <ServiceMetric label="Configured integrations" value={formatInteger(Object.keys(data.integrations).length)} />
        <ServiceMetric label="Routes exposed" value={formatInteger(configuredLinks.length)} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Service health</CardTitle>
          <CardDescription>Shows the core platform path and whether the runtime is reachable.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.services.map((service) => (
            <div key={service.name} className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-slate-50">{service.name}</div>
                <StatusBadge value={titleize(service.status)} />
              </div>
              <div className="mt-3 text-sm text-slate-400">{service.endpoint}</div>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_0.95fr]">
        <Card>
          <CardHeader>
            <CardTitle>Integrations</CardTitle>
            <CardDescription>Operational integrations and whether each one is live or still in demo relay mode.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {Object.entries(data.integrations).map(([name, value]) => (
              <div key={name} className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-medium text-slate-50">{titleize(name)}</div>
                  <StatusBadge value={String(value.live_configured ? "Live" : value.mode ?? "Unknown")} />
                </div>
                <div className="mt-2 text-sm text-slate-400">
                  Mode: {String(value.mode ?? "unknown")} · Configured: {String(value.configured ?? false)}
                </div>
                {name === "plane" && !value.live_configured ? (
                  <div className="mt-2 text-sm text-slate-500">
                    No standalone Plane route is exposed in demo-relay mode. Open Plane-linked tickets from the incident page instead.
                  </div>
                ) : null}
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Navigation links</CardTitle>
            <CardDescription>Only shown when a public route or URL is available to open directly.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {configuredLinks.length ? (
              configuredLinks.map((link) => (
                <div key={link.label} className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
                  <div>
                    <div className="font-medium text-slate-50">{link.label}</div>
                    <div className="text-sm text-slate-500">{link.url}</div>
                  </div>
                  <Button asChild variant="secondary">
                    <a href={link.url} target="_blank" rel="noreferrer">
                      <ExternalLink className="mr-2 h-4 w-4" />
                      Open
                    </a>
                  </Button>
                </div>
              ))
            ) : (
              <div className="text-sm text-slate-500">No public route links have been configured for this deployment yet.</div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function ServiceMetric({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}
