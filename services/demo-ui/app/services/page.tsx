"use client";

import * as React from "react";
import { ExternalLink } from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { TransientDataWarning } from "@/components/transient-data-warning";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConsoleStateQuery } from "@/lib/api";
import { formatInteger, isExternalUrl, titleize } from "@/lib/utils";

type IntegrationStatus = Record<string, unknown>;

type RouteLink = {
  label: string;
  url: string;
};

function clusterRouteUrl(origin: string, routeName: string, namespace?: string, path = "") {
  if (!origin) {
    return "";
  }
  try {
    const parsed = new URL(origin);
    const hostParts = parsed.host.split(".");
    if (hostParts.length < 2) {
      return "";
    }
    const clusterDomain = hostParts.slice(1).join(".");
    const routeHost = namespace ? `${routeName}-${namespace}.${clusterDomain}` : `${routeName}.${clusterDomain}`;
    return `${parsed.protocol}//${routeHost}${path}`;
  } catch {
    return "";
  }
}

function pickPreferredUrl(...values: Array<string | undefined>) {
  for (const value of values) {
    const candidate = String(value ?? "").trim();
    if (isExternalUrl(candidate)) {
      return candidate;
    }
  }
  return "";
}

function buildRouteLinks(origin: string, integrations: Record<string, IntegrationStatus>) {
  const aapIntegration = integrations.aap ?? {};
  const edaIntegration = integrations.eda ?? {};
  const planeIntegration = integrations.plane ?? {};
  const links: RouteLink[] = [
    { label: "Grafana", url: pickPreferredUrl(process.env.NEXT_PUBLIC_GRAFANA_URL, clusterRouteUrl(origin, "grafana", "ani-observability")) },
    {
      label: "Control Plane",
      url: pickPreferredUrl(process.env.NEXT_PUBLIC_CONTROL_PLANE_URL, clusterRouteUrl(origin, "control-plane", "ani-runtime", "/docs")),
    },
    { label: "Feature Gateway", url: clusterRouteUrl(origin, "feature-gateway", "ani-runtime", "/docs") },
    { label: "Milvus Attu", url: pickPreferredUrl(process.env.NEXT_PUBLIC_ATTU_URL, clusterRouteUrl(origin, "milvus-attu", "ani-data")) },
    { label: "OpenIMS WebUI", url: clusterRouteUrl(origin, "openimss-webui", "ani-sipp") },
    { label: "Feature Store UI", url: clusterRouteUrl(origin, "feast-ani-featurestore-ui", "ani-datascience") },
    { label: "MinIO Console", url: clusterRouteUrl(origin, "model-storage-minio-console", "ani-data") },
    {
      label: "AAP Controller",
      url: aapIntegration.live_configured
        ? pickPreferredUrl(String(aapIntegration.controller_app_url ?? ""), clusterRouteUrl(origin, "aap-controller", "aap"))
        : "",
    },
    {
      label: "AAP EDA",
      url: edaIntegration.live_configured
        ? pickPreferredUrl(String(edaIntegration.eda_app_url ?? ""), clusterRouteUrl(origin, "aap-eda", "aap"))
        : "",
    },
    {
      label: "Plane",
      url: planeIntegration.configured
        ? pickPreferredUrl(String(planeIntegration.app_url ?? ""), process.env.NEXT_PUBLIC_PLANE_URL, clusterRouteUrl(origin, "plane"))
        : "",
    },
  ];
  const uniqueLinks = new Map<string, RouteLink>();
  for (const item of links) {
    if (isExternalUrl(item.url) && !uniqueLinks.has(item.label)) {
      uniqueLinks.set(item.label, item);
    }
  }
  return Array.from(uniqueLinks.values());
}

export default function ServicesPage() {
  const { data, isLoading, error } = useConsoleStateQuery(30_000);
  const [origin, setOrigin] = React.useState("");

  React.useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  if (isLoading && !data) {
    return <div className="text-sm text-[var(--text-muted)]">Loading services...</div>;
  }
  if (!data) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load service data.</div>;
  }
  const showRefreshWarning = Boolean(error);

  const routeLinks = buildRouteLinks(origin, data.integrations);
  const configuredLinks = routeLinks.filter((item) => isExternalUrl(item.url));
  const aapIntegration = (data.integrations.aap ?? {}) as IntegrationStatus;
  const edaIntegration = (data.integrations.eda ?? {}) as IntegrationStatus;
  const controllerActions = asIntegrationItems(aapIntegration.actions);
  const edaPolicies = asIntegrationItems(edaIntegration.policies);

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Platform connectivity"
        title="Services"
        description="Service health, integration status, and direct links when they are available."
      />
      {showRefreshWarning ? (
        <TransientDataWarning>
          Showing the last successful service snapshot while the background refresh is retried.
        </TransientDataWarning>
      ) : null}

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
            <div key={service.name} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-[var(--text-strong)]">{service.name}</div>
                <StatusBadge value={titleize(service.status)} />
              </div>
              <div className="mt-3 text-sm text-[var(--text-secondary)]">{service.endpoint}</div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Automation delivery</CardTitle>
          <CardDescription>Manual incident actions stay in the incident workflow, while selected low-risk cases can also run through Event-Driven Ansible.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 xl:grid-cols-2">
          <div className="space-y-3">
            <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Manual from incident UI</div>
            {controllerActions.length ? (
              controllerActions.map((item) => (
                <div key={String(item.action ?? item.name ?? "action")} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-[var(--text-strong)]">{String(item.name ?? item.action ?? "AAP template")}</div>
                      <div className="mt-1 text-sm text-[var(--text-secondary)]">{String(item.description ?? item.playbook ?? "Controller-backed manual remediation.")}</div>
                    </div>
                    <StatusBadge value={item.template_exists ? "Ready" : "Pending"} />
                  </div>
                  <div className="mt-3 text-xs text-[var(--text-subtle)]">
                    {renderCases(item.cases)}{item.playbook ? ` · ${String(item.playbook)}` : ""}
                  </div>
                </div>
              ))
            ) : (
              <div className="text-sm text-[var(--text-subtle)]">No controller-backed manual actions are being reported yet.</div>
            )}
          </div>

          <div className="space-y-3">
            <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Event-Driven Ansible</div>
            {edaPolicies.length ? (
              edaPolicies.map((item) => (
                <div key={String(item.policy_key ?? item.name ?? "policy")} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-[var(--text-strong)]">{String(item.name ?? "EDA policy")}</div>
                      <div className="mt-1 text-sm text-[var(--text-secondary)]">
                        {String(item.action_summary ?? item.description ?? "Event-driven policy")}
                      </div>
                    </div>
                    <StatusBadge value={String(item.status ?? (item.activation_exists ? "Ready" : "Pending"))} />
                  </div>
                  <div className="mt-3 text-xs text-[var(--text-subtle)]">
                    Events: {renderList(item.event_types)} · Cases: {renderCases(item.cases)}
                  </div>
                  <div className="mt-1 text-xs text-[var(--text-subtle)]">
                    {item.activation_exists ? "Activation present in EDA." : "Activation has not been bootstrapped yet."}
                  </div>
                </div>
              ))
            ) : (
              <div className="text-sm text-[var(--text-subtle)]">No event-driven policies are being reported yet.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_0.95fr]">
        <Card>
          <CardHeader>
            <CardTitle>Integrations</CardTitle>
            <CardDescription>Integration status and whether each one is active or using demo mode.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {Object.entries(data.integrations).map(([name, value]) => (
              <div key={name} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-medium text-[var(--text-strong)]">{titleize(name)}</div>
                  <StatusBadge value={String(value.live_configured ? "Live" : value.mode ?? "Unknown")} />
                </div>
                <div className="mt-2 text-sm text-[var(--text-secondary)]">
                  Mode: {String(value.mode ?? "unknown")} · Configured: {String(value.configured ?? false)}
                </div>
                {name === "plane" && !value.live_configured ? (
                  <div className="mt-2 text-sm text-[var(--text-subtle)]">
                    Plane is not exposed as a separate link in this setup. Open linked tickets from the incident page instead.
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
                <div
                  key={link.label}
                  className="flex items-center justify-between rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4"
                >
                  <div>
                    <div className="font-medium text-[var(--text-strong)]">{link.label}</div>
                    <div className="text-sm text-[var(--text-subtle)]">{link.url}</div>
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
              <div className="text-sm text-[var(--text-subtle)]">No public route links have been configured for this deployment yet.</div>
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

function asIntegrationItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) : [];
}

function renderList(value: unknown) {
  if (!Array.isArray(value) || !value.length) {
    return "n/a";
  }
  return value.map((item) => String(item)).join(", ");
}

function renderCases(value: unknown) {
  const rendered = renderList(value);
  return rendered === "n/a" ? "Cases not declared" : rendered.replace(/_/g, " ");
}
