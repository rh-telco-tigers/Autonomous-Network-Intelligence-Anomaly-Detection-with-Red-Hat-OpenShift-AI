SHELL := /bin/sh

DEMO_NAMESPACE ?= ims-demo-lab
MODEL_REGISTRY_NAMESPACE ?= rhoai-model-registries
MODEL_REGISTRY_SERVICE ?= ims-demo-modelregistry
INCIDENT_RELEASE_DATASET_VERSION ?=
DEMO_TRIGGER_DIR := k8s/manual/demo-triggers

.PHONY: help kustomize-demo apply-demo-ai-extras check-demo-incident-generators check-fresh-cluster-gitops check-fresh-cluster-ai check-fresh-cluster-runtime check-fresh-cluster validate-python repo-tree trigger-build-pipeline trigger-anomaly-platform-pipeline trigger-feature-bundle-pipeline trigger-featurestore-pipeline trigger-incident-release-pipeline smoke-check-featurestore-serving trigger-incident-release stop-incident-release

help: ## Print available make targets
	@printf "Available commands:\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / { names[++count] = $$1; desc[count] = $$2; if (length($$1) > width) width = length($$1) } END { for (i = 1; i <= count; i++) printf "  %-" width "s  %s\n", names[i], desc[i] }' $(MAKEFILE_LIST)

kustomize-demo: ## Render the demo overlay manifests
	kustomize build k8s/overlays/demo

apply-demo-ai-extras: ## Imperatively apply AI extras as a recovery path
	oc apply -k k8s/base/feature-store
	oc apply -k k8s/base/kafka
	oc apply -k k8s/base/kfp

check-demo-incident-generators: ## List the demo pulse and SIPp cronjobs
	oc get cronjob -n "$(DEMO_NAMESPACE)" | rg 'demo-incident-pulse|sipp-'

check-fresh-cluster-gitops: ## Check GitOps applications after bootstrap
	oc get application.argoproj.io ims-demo-operators -n openshift-gitops
	oc get application.argoproj.io ims-demo-platform -n openshift-gitops

check-fresh-cluster-ai: ## Check AI, serving, and model registry readiness
	oc get dspa,featurestore,kafka -n "$(DEMO_NAMESPACE)"
	oc get workflow -n "$(DEMO_NAMESPACE)"
	oc get inferenceservice -n "$(DEMO_NAMESPACE)" | rg 'ims-predictive-fs|ims-predictive-fs-mlserver'
	oc get svc -n "$(MODEL_REGISTRY_NAMESPACE)" | rg "$(MODEL_REGISTRY_SERVICE)"

check-fresh-cluster-runtime: ## Check runtime services and incident generators
	oc get deploy -n "$(DEMO_NAMESPACE)"
	oc get svc -n "$(DEMO_NAMESPACE)" | rg 'control-plane|feature-gateway|anomaly-service|rca-service|demo-ui'
	$(MAKE) check-demo-incident-generators

check-fresh-cluster: ## Run the full fresh-cluster verification checklist
	$(MAKE) check-fresh-cluster-gitops
	$(MAKE) check-fresh-cluster-ai
	$(MAKE) check-fresh-cluster-runtime

validate-python: ## Compile Python sources for a quick syntax check
	python3 -m compileall services ai

repo-tree: ## List repository files
	rg --files .

trigger-build-pipeline: ## Start the demo Tekton image build
	@printf "Creating demo build PipelineRun in %s\n" "$(DEMO_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/tekton-build-pipelinerun.yaml"

trigger-anomaly-platform-pipeline: ## Start a fresh demo anomaly training run
	@printf "Creating demo KFP trigger job for ims-anomaly-platform-train-and-register in %s\n" "$(DEMO_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/anomaly-platform-run-job.yaml"

trigger-feature-bundle-pipeline: ## Start a fresh demo feature bundle publish run
	@printf "Creating demo KFP trigger job for ims-feature-bundle-publish in %s\n" "$(DEMO_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/feature-bundle-run-job.yaml"

trigger-featurestore-pipeline: ## Start a fresh demo feature-store training run
	@printf "Creating demo KFP trigger job for ims-featurestore-train-and-register in %s\n" "$(DEMO_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/featurestore-run-job.yaml"

trigger-incident-release-pipeline: ## Start a fresh manual incident-release KFP run
	@printf "Creating demo KFP trigger job for ims-incident-release in %s\n" "$(DEMO_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/incident-release-run-job.yaml"

smoke-check-featurestore-serving: ## Run the demo feature-store serving smoke check
	@printf "Creating feature-store serving smoke check job in %s\n" "$(DEMO_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/featurestore-serving-smoke-job.yaml"

trigger-incident-release: ## Start a fresh manual 100k backfill dataset
	@dataset_version="$${INCIDENT_RELEASE_DATASET_VERSION:-backfill-sipp-100k-$$(date +%Y%m%d-%H%M%S)}"; \
	printf "Creating manual backfill jobs for dataset %s in %s\n" "$$dataset_version" "$(DEMO_NAMESPACE)"; \
	kustomize build "k8s/manual/traffic-backfill-100k" \
	  | python3 "k8s/manual/traffic-backfill-100k/render_jobs.py" --dataset-version "$$dataset_version" \
	  | oc create -f -; \
	printf "Watch jobs: oc get jobs -n %s -l app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=%s\n" "$(DEMO_NAMESPACE)" "$$dataset_version"; \
	printf "Watch pods: oc get pods -n %s -l app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=%s\n" "$(DEMO_NAMESPACE)" "$$dataset_version"; \
	printf "Stop run: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=%s\n" "$$dataset_version"

stop-incident-release: ## Stop and delete one backfill dataset version
	@[ -n "$(INCIDENT_RELEASE_DATASET_VERSION)" ] || { \
	  printf "Set INCIDENT_RELEASE_DATASET_VERSION, for example: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=backfill-sipp-100k-20260401-120000\n"; \
	  exit 1; \
	}
	@printf "Deleting manual backfill jobs for dataset %s in %s\n" "$(INCIDENT_RELEASE_DATASET_VERSION)" "$(DEMO_NAMESPACE)"
	oc delete jobs -n "$(DEMO_NAMESPACE)" -l "app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=$(INCIDENT_RELEASE_DATASET_VERSION)" --ignore-not-found

