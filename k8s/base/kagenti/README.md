This base slice keeps the vendored upstream Kagenti Helm charts inside the same
`k8s/base` hierarchy as the rest of the platform assets.

Contents:
- `charts/kagenti/` for the main Kagenti chart
- `charts/kagenti-deps/` for the Kagenti dependency chart

The charts are stored here for clean repo organization and future GitOps
integration work. They are not yet rendered into this repo's root base
Kustomization output.
