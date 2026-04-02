SHELL := /bin/zsh

INCIDENT_RELEASE_DATASET_VERSION ?=
DEMO_TRIGGER_DIR := k8s/manual/demo-triggers

.PHONY: help kustomize-demo validate-python repo-tree trigger-build-pipeline trigger-anomaly-platform-pipeline trigger-feature-bundle-pipeline trigger-featurestore-pipeline smoke-check-featurestore-serving trigger-incident-release stop-incident-release

help: ## Print available make targets
	@printf "Available commands:\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / { names[++count] = $$1; desc[count] = $$2; if (length($$1) > width) width = length($$1) } END { for (i = 1; i <= count; i++) printf "  %-" width "s  %s\n", names[i], desc[i] }' $(MAKEFILE_LIST)

kustomize-demo: ## Render the demo overlay manifests
	kustomize build k8s/overlays/demo

validate-python: ## Compile Python sources for a quick syntax check
	python3 -m compileall services ai

repo-tree: ## List repository files
	rg --files .

trigger-build-pipeline: ## Start the demo Tekton image build
	@printf "Creating demo build PipelineRun in ims-demo-lab\n"
	oc create -f "$(DEMO_TRIGGER_DIR)/tekton-build-pipelinerun.yaml"

trigger-anomaly-platform-pipeline: ## Start a fresh demo anomaly training run
	@printf "Creating demo KFP trigger job for ims-anomaly-platform-train-and-register in ims-demo-lab\n"
	oc create -f "$(DEMO_TRIGGER_DIR)/anomaly-platform-run-job.yaml"

trigger-feature-bundle-pipeline: ## Start a fresh demo feature bundle publish run
	@printf "Creating demo KFP trigger job for ims-feature-bundle-publish in ims-demo-lab\n"
	oc create -f "$(DEMO_TRIGGER_DIR)/feature-bundle-run-job.yaml"

trigger-featurestore-pipeline: ## Start a fresh demo feature-store training run
	@printf "Creating demo KFP trigger job for ims-featurestore-train-and-register in ims-demo-lab\n"
	oc create -f "$(DEMO_TRIGGER_DIR)/featurestore-run-job.yaml"

smoke-check-featurestore-serving: ## Run the demo feature-store serving smoke check
	@printf "Creating feature-store serving smoke check job in ims-demo-lab\n"
	oc create -f "$(DEMO_TRIGGER_DIR)/featurestore-serving-smoke-job.yaml"

trigger-incident-release: ## Start a fresh manual 100k backfill dataset
	@dataset_version="$${INCIDENT_RELEASE_DATASET_VERSION:-backfill-sipp-100k-$$(date +%Y%m%d-%H%M%S)}"; \
	printf "Creating manual backfill jobs for dataset %s in ims-demo-lab\n" "$$dataset_version"; \
	kustomize build "k8s/manual/traffic-backfill-100k" \
	  | python3 "k8s/manual/traffic-backfill-100k/render_jobs.py" --dataset-version "$$dataset_version" \
	  | oc create -f -; \
	printf "Watch jobs: oc get jobs -n ims-demo-lab -l app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=%s\n" "$$dataset_version"; \
	printf "Watch pods: oc get pods -n ims-demo-lab -l app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=%s\n" "$$dataset_version"; \
	printf "Stop run: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=%s\n" "$$dataset_version"

stop-incident-release: ## Stop and delete one backfill dataset version
	@[ -n "$(INCIDENT_RELEASE_DATASET_VERSION)" ] || { \
	  printf "Set INCIDENT_RELEASE_DATASET_VERSION, for example: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=backfill-sipp-100k-20260401-120000\n"; \
	  exit 1; \
	}
	@printf "Deleting manual backfill jobs for dataset %s in ims-demo-lab\n" "$(INCIDENT_RELEASE_DATASET_VERSION)"
	oc delete jobs -n "ims-demo-lab" -l "app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=$(INCIDENT_RELEASE_DATASET_VERSION)" --ignore-not-found

