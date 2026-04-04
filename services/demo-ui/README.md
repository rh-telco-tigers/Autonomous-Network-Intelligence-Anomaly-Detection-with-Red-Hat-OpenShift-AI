# demo-ui

Production-ready operator UI for the IMS anomaly workflow.

## Stack

- Next.js App Router
- TypeScript
- Tailwind CSS
- TanStack Query
- TanStack Table
- React Hook Form + Zod
- Recharts

## Navigation

The UI is intentionally split into focused pages instead of a single overloaded dashboard:

- `Overview` for high-level metrics and charts
- `Incidents` for queue management
- `Incident detail` for RCA, remediation, ticket sync, execution, and verification
- `Live Traffic` for the normal/anomalous stream
- `Services` for platform health and integration links
- `Demo Scenarios` for on-demand scenario execution

## Proxy model

The Next.js server rewrites same-origin `/api/*` requests to the in-cluster `control-plane` service using `CONTROL_PLANE_PROXY_URL`.

That keeps browser traffic on the `demo-ui` route while still allowing the UI to call the control-plane API without separate frontend CORS work.
