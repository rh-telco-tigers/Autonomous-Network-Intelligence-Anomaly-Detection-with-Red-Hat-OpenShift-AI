SHELL := /bin/zsh

INCIDENT_RELEASE_BACKFILL_PATH := k8s/manual/traffic-backfill-100k

.PHONY: help kustomize-demo validate-python repo-tree trigger-incident-release stop-incident-release

help: ## Print available make targets
	@printf "Available commands:\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-24s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

kustomize-demo: ## Render the demo overlay manifests
	kustomize build k8s/overlays/demo

validate-python: ## Compile Python sources for a quick syntax check
	python3 -m compileall services ai

repo-tree: ## List repository files
	rg --files .

trigger-incident-release: ## Start the manual 100k incident-release backfill jobs
	oc apply -k $(INCIDENT_RELEASE_BACKFILL_PATH)

stop-incident-release: ## Stop and delete the manual 100k incident-release backfill jobs
	oc delete -k $(INCIDENT_RELEASE_BACKFILL_PATH) --ignore-not-found

