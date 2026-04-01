# demo-ui

Provides the operator-facing dashboard for the IMS anomaly demo.

It lets a user run traffic scenarios, watch health and model status, and review the latest incidents, RCA results, and approval actions.

The container now serves the UI and proxies same-origin `/api/*` requests to the in-cluster `control-plane` service. That avoids browser failures on clusters where the public routes use self-signed certificates, because the browser only talks to the `demo-ui` route directly.
