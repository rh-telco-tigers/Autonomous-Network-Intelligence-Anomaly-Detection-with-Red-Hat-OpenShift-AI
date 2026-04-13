SHELL := /bin/sh

SIPP_NAMESPACE ?= ani-sipp
RUNTIME_NAMESPACE ?= ani-runtime
DATA_NAMESPACE ?= ani-data
DATASCIENCE_NAMESPACE ?= ani-datascience
TEKTON_NAMESPACE ?= ani-tekton
MODEL_REGISTRY_NAMESPACE ?= rhoai-model-registries
MODEL_REGISTRY_SERVICE ?= default-modelregistry
MODEL_REGISTRY_ENDPOINT ?= http://$(MODEL_REGISTRY_SERVICE).$(MODEL_REGISTRY_NAMESPACE).svc.cluster.local:8080
INCIDENT_RELEASE_DATASET_VERSION ?=
INCIDENT_RELEASE_SOURCE_DATASET_VERSION ?=
INCIDENT_RELEASE_LINKED_DATASET_VERSION ?= live-sipp-v1
BACKFILL_DATASET_VERSION ?= backfill-sipp-100k
BACKFILL_BUNDLE_VERSION ?= ani-backfill-feature-bundle-v1
BACKFILL_FEATURE_SERVICE_NAME ?= ani_anomaly_scoring_v1
BACKFILL_CANDIDATE_VERSION ?= candidate-backfill-fs-v1
BACKFILL_MODEL_NAME ?= ani-anomaly-featurestore-backfill
BACKFILL_MODEL_VERSION_NAME ?= ani-anomaly-featurestore-backfill-v1
BACKFILL_SERVING_MODEL_NAME ?= ani-predictive-backfill
BACKFILL_SERVING_RUNTIME_NAME ?= ani-autogluon-mlserver-runtime
BACKFILL_SERVING_PREFIX ?= predictive-featurestore
INCIDENT_RELEASE_VERSION ?=
INCIDENT_RELEASE_MODE ?= draft-replacement
INCIDENT_RELEASE_PUBLIC_RECORD_TARGET ?= 10000
INCIDENT_RELEASE_PREVIOUS_VERSION ?=
DEMO_PROJECT ?= ani-demo
DEMO_INCIDENT_SCENARIO ?= busy_destination
CONTROL_PLANE_API_TOKEN ?= demo-token
DEMO_TRIGGER_DIR := k8s/manual/demo-triggers
MACHINE_API_MANUAL_DIR := k8s/manual/machine-api
GPU_MACHINESET_RENDERER := $(MACHINE_API_MANUAL_DIR)/render_gpu_machineset.py
GPU_INSTANCE_TYPE ?= g6.8xlarge
GPU_REPLICAS ?= 1
GPU_SOURCE_MACHINESET ?=
GPU_OUTPUT ?=

.PHONY: help kustomize-gitops apply-demo-ai-extras check-demo-incident-generators check-fresh-cluster-gitops check-fresh-cluster-ai check-fresh-cluster-runtime check-fresh-cluster validate-python repo-tree render-gpu-node-pool add-gpu-node-pool trigger-build-pipeline live-step-1-generate-demo-incident live-step-2-build-incident-release live-step-3-publish-feature-bundle live-step-4-train-and-deploy-classifier live-step-5-smoke-check-serving backfill-step-1-generate-training-dataset backfill-step-2-build-feature-bundle backfill-step-3-train-and-register-classifier backfill-step-4-activate-serving-endpoint backfill-step-5-smoke-check-serving step-1-generate-demo-incident step-2-backfill-training-dataset step-3-build-incident-release step-4-publish-feature-bundle step-5-train-and-deploy-classifier legacy-train-and-deploy-classifier smoke-check-featurestore-serving stop-incident-release list-incident-release-datasets generate-demo-incident trigger-anomaly-platform-pipeline trigger-feature-bundle-pipeline trigger-featurestore-pipeline trigger-incident-release-pipeline trigger-incident-release backfill-step-4-smoke-check-serving

help: ## Print available make targets
	@printf "Available commands:\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / { names[++count] = $$1; desc[count] = $$2; if (length($$1) > width) width = length($$1) } END { for (i = 1; i <= count; i++) printf "  %-" width "s  %s\n", names[i], desc[i] }' $(MAKEFILE_LIST)

kustomize-gitops: ## Render the split GitOps application set
	kustomize build deploy/gitops/apps

apply-demo-ai-extras: ## Imperatively apply AI extras as a recovery path
	oc apply -k k8s/base/feature-store
	oc apply -k k8s/base/kafka
	oc apply -k k8s/base/kfp

check-demo-incident-generators: ## List the demo pulse and SIPp cronjobs
	oc get cronjob -n "$(SIPP_NAMESPACE)" | rg 'sipp-'
	oc get cronjob -n "$(RUNTIME_NAMESPACE)" | rg 'demo-incident-pulse'

check-fresh-cluster-gitops: ## Check GitOps applications after bootstrap
	oc get application.argoproj.io ani-operators -n openshift-gitops
	oc get application.argoproj.io ani-platform -n openshift-gitops

check-fresh-cluster-ai: ## Check AI, serving, and model registry readiness
	oc get dspa,featurestore -n "$(DATASCIENCE_NAMESPACE)"
	oc get kafka -n "$(DATA_NAMESPACE)"
	oc get workflow -n "$(DATASCIENCE_NAMESPACE)"
	oc get inferenceservice -n "$(DATASCIENCE_NAMESPACE)" | rg 'ani-predictive-fs|ani-predictive-backfill|ani-predictive-fs-mlserver'
	oc get modelregistry -n "$(MODEL_REGISTRY_NAMESPACE)"
	oc get svc -n "$(MODEL_REGISTRY_NAMESPACE)" "$(MODEL_REGISTRY_SERVICE)"

check-fresh-cluster-runtime: ## Check runtime services and incident generators
	oc get deploy -n "$(RUNTIME_NAMESPACE)"
	oc get deploy -n "$(SIPP_NAMESPACE)" | rg 'ims-|openimss'
	oc get svc -n "$(RUNTIME_NAMESPACE)" | rg 'control-plane|feature-gateway|anomaly-service|rca-service|demo-ui'
	$(MAKE) check-demo-incident-generators

check-fresh-cluster: ## Run the full fresh-cluster verification checklist
	$(MAKE) check-fresh-cluster-gitops
	$(MAKE) check-fresh-cluster-ai
	$(MAKE) check-fresh-cluster-runtime

validate-python: ## Compile Python sources for a quick syntax check
	python3 -m compileall services ai

repo-tree: ## List repository files
	rg --files .

render-gpu-node-pool: ## Render a manual AWS GPU MachineSet from the current cluster
	@python3 "$(GPU_MACHINESET_RENDERER)" \
	  --instance-type="$(GPU_INSTANCE_TYPE)" \
	  --replicas="$(GPU_REPLICAS)" \
	  $(if $(GPU_SOURCE_MACHINESET),--source-machineset="$(GPU_SOURCE_MACHINESET)") \
	  $(if $(GPU_OUTPUT),--output="$(GPU_OUTPUT)")

add-gpu-node-pool: ## Render and manually apply a GPU MachineSet to the current cluster
	@python3 "$(GPU_MACHINESET_RENDERER)" \
	  --instance-type="$(GPU_INSTANCE_TYPE)" \
	  --replicas="$(GPU_REPLICAS)" \
	  $(if $(GPU_SOURCE_MACHINESET),--source-machineset="$(GPU_SOURCE_MACHINESET)") \
	  $(if $(GPU_OUTPUT),--output="$(GPU_OUTPUT)") \
	  --apply

trigger-build-pipeline: ## Start the demo Tekton image build
	@branch="$$(git rev-parse --abbrev-ref HEAD)"; \
	printf "Waiting for Tekton pipeline assets in %s before triggering the build\n" "$(TEKTON_NAMESPACE)"; \
	for resource in \
	  "pipelines.tekton.dev/ani-platform-container-build" \
	  "tasks.tekton.dev/git-clone-lite" \
	  "tasks.tekton.dev/buildah-lite" \
	  "tasks.tekton.dev/buildah-heavy"; do \
	  for attempt in $$(seq 1 60); do \
	    if oc get "$$resource" -n "$(TEKTON_NAMESPACE)" >/dev/null 2>&1; then \
	      break; \
	    fi; \
	    if [ "$$attempt" -eq 60 ]; then \
	      echo "Tekton asset $$resource is still missing in $(TEKTON_NAMESPACE)."; \
	      echo "Wait for ani-tekton to finish syncing, then rerun make trigger-build-pipeline."; \
	      exit 1; \
	    fi; \
	    sleep 5; \
	  done; \
	done; \
	printf "Creating demo build PipelineRun for branch %s in %s\n" "$$branch" "$(TEKTON_NAMESPACE)"; \
	GIT_BRANCH="$$branch" python3 -c 'from pathlib import Path; import os; manifest = Path("$(DEMO_TRIGGER_DIR)/tekton-build-pipelinerun.yaml").read_text(); print(manifest.replace("__GIT_REVISION__", os.environ["GIT_BRANCH"]), end="")' | oc create -f -

live-step-1-generate-demo-incident: ## Live Step 1: Create one live incident in the demo app by calling the control-plane scenario endpoint; set DEMO_INCIDENT_SCENARIO=<scenario> if needed
	@set -e; \
	control_plane_host="$$(oc get route control-plane -n "$(RUNTIME_NAMESPACE)" -o jsonpath='{.spec.host}')"; \
	printf "Creating one live incident for scenario %s through %s\n" "$(DEMO_INCIDENT_SCENARIO)" "$$control_plane_host"; \
	curl -ksSf "https://$$control_plane_host/console/run-scenario" \
	  -H "x-api-key: $(CONTROL_PLANE_API_TOKEN)" \
	  -H "Content-Type: application/json" \
	  -d "{\"scenario\":\"$(DEMO_INCIDENT_SCENARIO)\",\"project\":\"$(DEMO_PROJECT)\"}" | python3 -m json.tool

step-1-generate-demo-incident: live-step-1-generate-demo-incident

generate-demo-incident: live-step-1-generate-demo-incident

live-step-2-build-incident-release: ## Live Step 2: Compile one incident-release bundle from the incident-linked live dataset; backfill datasets are rejected
	@source_dataset_version="$(INCIDENT_RELEASE_SOURCE_DATASET_VERSION)"; \
	if [ -z "$$source_dataset_version" ]; then \
	  source_dataset_version="$(INCIDENT_RELEASE_LINKED_DATASET_VERSION)"; \
	fi; \
	if [ -z "$$source_dataset_version" ]; then \
	  printf "No source dataset version was selected.\n"; \
	  printf "Set INCIDENT_RELEASE_LINKED_DATASET_VERSION or pass INCIDENT_RELEASE_SOURCE_DATASET_VERSION.\n"; \
	  exit 1; \
	fi; \
	case "$$source_dataset_version" in \
	  backfill-sipp-100k*) \
	    printf "Dataset %s is a backfill-only dataset and cannot be used for incident release.\n" "$$source_dataset_version"; \
	    printf "Use the incident-linked dataset instead: make live-step-2-build-incident-release INCIDENT_RELEASE_SOURCE_DATASET_VERSION=%s\n" "$(INCIDENT_RELEASE_LINKED_DATASET_VERSION)"; \
	    exit 1; \
	    ;; \
	esac; \
	release_version="$${INCIDENT_RELEASE_VERSION:-$${source_dataset_version}-draft}"; \
	printf "Creating demo KFP trigger job for ani-incident-release in %s (source_dataset_version=%s, release_version=%s)\n" "$(DATASCIENCE_NAMESPACE)" "$$source_dataset_version" "$$release_version"; \
	printf "Incident release always uses the incident-linked dataset unless you explicitly override it with another linked dataset.\n"; \
	INCIDENT_RELEASE_SOURCE_DATASET_VERSION="$$source_dataset_version" \
	INCIDENT_RELEASE_VERSION="$$release_version" \
	INCIDENT_RELEASE_MODE="$(INCIDENT_RELEASE_MODE)" \
	INCIDENT_RELEASE_PUBLIC_RECORD_TARGET="$(INCIDENT_RELEASE_PUBLIC_RECORD_TARGET)" \
	INCIDENT_RELEASE_PREVIOUS_VERSION="$(INCIDENT_RELEASE_PREVIOUS_VERSION)" \
	python3 -c 'from pathlib import Path; import functools, os; manifest = Path("$(DEMO_TRIGGER_DIR)/incident-release-run-job.yaml").read_text(); replacements = {"__INCIDENT_RELEASE_SOURCE_DATASET_VERSION__": os.environ["INCIDENT_RELEASE_SOURCE_DATASET_VERSION"], "__INCIDENT_RELEASE_VERSION__": os.environ["INCIDENT_RELEASE_VERSION"], "__INCIDENT_RELEASE_MODE__": os.environ["INCIDENT_RELEASE_MODE"], "__INCIDENT_RELEASE_PUBLIC_RECORD_TARGET__": os.environ["INCIDENT_RELEASE_PUBLIC_RECORD_TARGET"], "__INCIDENT_RELEASE_PREVIOUS_VERSION__": os.environ["INCIDENT_RELEASE_PREVIOUS_VERSION"]}; print(functools.reduce(lambda text, item: text.replace(item[0], item[1]), replacements.items(), manifest), end="")' | oc create -f -

step-3-build-incident-release: live-step-2-build-incident-release

trigger-incident-release-pipeline: live-step-2-build-incident-release

live-step-3-publish-feature-bundle: ## Live Step 3: Publish the feature-store-ready live bundle dataset that downstream training consumes
	@printf "Creating demo KFP trigger job for ani-feature-bundle-publish in %s\n" "$(DATASCIENCE_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/feature-bundle-run-job.yaml"

step-4-publish-feature-bundle: live-step-3-publish-feature-bundle

trigger-feature-bundle-pipeline: live-step-3-publish-feature-bundle

live-step-4-train-and-deploy-classifier: ## Live Step 4: Train, register, and deploy the feature-store model to ani-predictive-fs, which the app uses for live classification
	@printf "Creating demo KFP trigger job for ani-featurestore-train-and-register in %s\n" "$(DATASCIENCE_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/featurestore-run-job.yaml"

step-5-train-and-deploy-classifier: live-step-4-train-and-deploy-classifier

trigger-featurestore-pipeline: live-step-4-train-and-deploy-classifier

legacy-train-and-deploy-classifier: ## Legacy: Train and deploy the older MinIO-only classifier path; keep only for compatibility, not for the preferred app path
	@printf "Creating demo KFP trigger job for ani-anomaly-platform-train-and-register in %s\n" "$(DATASCIENCE_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/anomaly-platform-run-job.yaml"

trigger-anomaly-platform-pipeline: legacy-train-and-deploy-classifier

live-step-5-smoke-check-serving: ## Live Step 5: Run a serving smoke check against the live feature-store predictive endpoint
	@printf "Creating feature-store serving smoke check job in %s\n" "$(DATASCIENCE_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/featurestore-serving-smoke-job.yaml"

smoke-check-featurestore-serving: live-step-5-smoke-check-serving

backfill-step-1-generate-training-dataset: ## Backfill Step 1: Generate the large shared backfill dataset used only for offline training and bundle publishing
	@dataset_version="$(BACKFILL_DATASET_VERSION)"; \
	if [ -n "$(INCIDENT_RELEASE_DATASET_VERSION)" ] && [ "$(INCIDENT_RELEASE_DATASET_VERSION)" != "$(BACKFILL_DATASET_VERSION)" ]; then \
	  printf "Backfill now uses a single shared dataset version: %s\n" "$(BACKFILL_DATASET_VERSION)"; \
	  printf "Custom backfill dataset versions are disabled.\n"; \
	  exit 1; \
	fi; \
	printf "Creating manual backfill jobs for dataset %s in %s\n" "$$dataset_version" "$(SIPP_NAMESPACE)"; \
	kustomize build "k8s/manual/traffic-backfill-100k" \
	  | python3 "k8s/manual/traffic-backfill-100k/render_jobs.py" --dataset-version "$$dataset_version" \
	  | oc create -f -; \
	printf "Watch jobs: oc get jobs -n %s -l app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=%s\n" "$(SIPP_NAMESPACE)" "$$dataset_version"; \
	printf "Watch pods: oc get pods -n %s -l app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=%s\n" "$(SIPP_NAMESPACE)" "$$dataset_version"; \
	printf "Backfill datasets are training-only and are not valid incident-release sources.\n"; \
	printf "Next backfill-model step: make backfill-step-2-build-feature-bundle\n"; \
	printf "Next live-model step: make live-step-2-build-incident-release\n"; \
	printf "Incident-linked dataset default: %s\n" "$(INCIDENT_RELEASE_LINKED_DATASET_VERSION)"; \
	printf "List versions later: make list-incident-release-datasets\n"; \
	printf "Stop run: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=%s\n" "$$dataset_version"

step-2-backfill-training-dataset: backfill-step-1-generate-training-dataset

trigger-incident-release: backfill-step-1-generate-training-dataset

backfill-step-2-build-feature-bundle: ## Backfill Step 2: Build a Kaggle-ready backfill bundle with parquet and CSV exports from the shared backfill dataset
	@printf "Creating backfill feature bundle trigger job in %s (dataset=%s, bundle=%s)\n" "$(DATASCIENCE_NAMESPACE)" "$(BACKFILL_DATASET_VERSION)" "$(BACKFILL_BUNDLE_VERSION)"; \
	BACKFILL_DATASET_VERSION="$(BACKFILL_DATASET_VERSION)" \
	BACKFILL_BUNDLE_VERSION="$(BACKFILL_BUNDLE_VERSION)" \
	DEMO_PROJECT="$(DEMO_PROJECT)" \
	python3 -c 'from pathlib import Path; import functools, os; manifest = Path("$(DEMO_TRIGGER_DIR)/backfill-feature-bundle-run-job.yaml").read_text(); replacements = {"__BACKFILL_DATASET_VERSION__": os.environ["BACKFILL_DATASET_VERSION"], "__BACKFILL_BUNDLE_VERSION__": os.environ["BACKFILL_BUNDLE_VERSION"], "__DEMO_PROJECT__": os.environ["DEMO_PROJECT"]}; print(functools.reduce(lambda text, item: text.replace(item[0], item[1]), replacements.items(), manifest), end="")' | oc create -f -

backfill-step-3-train-and-register-classifier: ## Backfill Step 3: Train the best AutoGluon backfill model, register it, and refresh the always-on backfill predictor artifact path
	@printf "Creating backfill featurestore training trigger job in %s (bundle=%s, serving_model=%s)\n" "$(DATASCIENCE_NAMESPACE)" "$(BACKFILL_BUNDLE_VERSION)" "$(BACKFILL_SERVING_MODEL_NAME)"; \
	BACKFILL_BUNDLE_VERSION="$(BACKFILL_BUNDLE_VERSION)" \
	BACKFILL_FEATURE_SERVICE_NAME="$(BACKFILL_FEATURE_SERVICE_NAME)" \
	BACKFILL_CANDIDATE_VERSION="$(BACKFILL_CANDIDATE_VERSION)" \
	BACKFILL_MODEL_NAME="$(BACKFILL_MODEL_NAME)" \
	BACKFILL_MODEL_VERSION_NAME="$(BACKFILL_MODEL_VERSION_NAME)" \
	BACKFILL_SERVING_MODEL_NAME="$(BACKFILL_SERVING_MODEL_NAME)" \
	BACKFILL_SERVING_RUNTIME_NAME="$(BACKFILL_SERVING_RUNTIME_NAME)" \
	BACKFILL_SERVING_PREFIX="$(BACKFILL_SERVING_PREFIX)" \
	MODEL_REGISTRY_ENDPOINT="$(MODEL_REGISTRY_ENDPOINT)" \
	MODEL_REGISTRY_NAMESPACE="$(MODEL_REGISTRY_NAMESPACE)" \
	MODEL_REGISTRY_SERVICE="$(MODEL_REGISTRY_SERVICE)" \
	python3 -c 'from pathlib import Path; import functools, os; manifest = Path("$(DEMO_TRIGGER_DIR)/backfill-featurestore-run-job.yaml").read_text(); replacements = {"__BACKFILL_BUNDLE_VERSION__": os.environ["BACKFILL_BUNDLE_VERSION"], "__BACKFILL_FEATURE_SERVICE_NAME__": os.environ["BACKFILL_FEATURE_SERVICE_NAME"], "__BACKFILL_CANDIDATE_VERSION__": os.environ["BACKFILL_CANDIDATE_VERSION"], "__BACKFILL_MODEL_NAME__": os.environ["BACKFILL_MODEL_NAME"], "__BACKFILL_MODEL_VERSION_NAME__": os.environ["BACKFILL_MODEL_VERSION_NAME"], "__BACKFILL_SERVING_MODEL_NAME__": os.environ["BACKFILL_SERVING_MODEL_NAME"], "__BACKFILL_SERVING_RUNTIME_NAME__": os.environ["BACKFILL_SERVING_RUNTIME_NAME"], "__BACKFILL_SERVING_PREFIX__": os.environ["BACKFILL_SERVING_PREFIX"], "__MODEL_REGISTRY_ENDPOINT__": os.environ["MODEL_REGISTRY_ENDPOINT"], "__MODEL_REGISTRY_NAMESPACE__": os.environ["MODEL_REGISTRY_NAMESPACE"], "__MODEL_REGISTRY_SERVICE__": os.environ["MODEL_REGISTRY_SERVICE"]}; print(functools.reduce(lambda text, item: text.replace(item[0], item[1]), replacements.items(), manifest), end="")' | oc create -f -

backfill-step-4-activate-serving-endpoint: ## Backfill Step 4: Create or refresh the backfill serving runtime and inference endpoint so it can stay active beside the live model
	@printf "Applying backfill serving resources in %s\n" "$(DATASCIENCE_NAMESPACE)"
	MODEL_REGISTRY_ENDPOINT="$(MODEL_REGISTRY_ENDPOINT)" \
	MODEL_REGISTRY_NAMESPACE="$(MODEL_REGISTRY_NAMESPACE)" \
	MODEL_REGISTRY_SERVICE="$(MODEL_REGISTRY_SERVICE)" \
	python3 "$(DEMO_TRIGGER_DIR)/render_backfill_serving_resources.py" \
	  --namespace "$(DATASCIENCE_NAMESPACE)" \
	  --model-name "$(BACKFILL_MODEL_NAME)" \
	  --model-version-name "$(BACKFILL_MODEL_VERSION_NAME)" \
	  --serving-model-name "$(BACKFILL_SERVING_MODEL_NAME)" \
	  --serving-runtime-name "$(BACKFILL_SERVING_RUNTIME_NAME)" \
	  --model-registry-endpoint "$(MODEL_REGISTRY_ENDPOINT)" \
	  --model-registry-namespace "$(MODEL_REGISTRY_NAMESPACE)" \
	  --model-registry-service "$(MODEL_REGISTRY_SERVICE)" \
	  --template "$(DEMO_TRIGGER_DIR)/backfill-serving-resources.yaml" | oc apply -f -
	oc wait --for=condition=Ready "inferenceservice/$(BACKFILL_SERVING_MODEL_NAME)" -n "$(DATASCIENCE_NAMESPACE)" --timeout=10m

backfill-step-5-smoke-check-serving: ## Backfill Step 5: Smoke-check the backfill predictor endpoint before switching the UI classifier profile to backfill
	@printf "Creating backfill serving smoke check job in %s\n" "$(DATASCIENCE_NAMESPACE)"
	oc create -f "$(DEMO_TRIGGER_DIR)/backfill-serving-smoke-job.yaml"

backfill-step-4-smoke-check-serving: backfill-step-5-smoke-check-serving

list-incident-release-datasets: ## Utility: List active backfill runs and stored dataset versions so Step 3 can reuse the right source dataset version
	python3 "k8s/manual/traffic-backfill-100k/list_dataset_versions.py" --sipp-namespace "$(SIPP_NAMESPACE)"

stop-incident-release: ## Stop and delete one backfill dataset version
	@dataset_version="$${INCIDENT_RELEASE_DATASET_VERSION:-$(BACKFILL_DATASET_VERSION)}"; \
	if [ "$$dataset_version" != "$(BACKFILL_DATASET_VERSION)" ]; then \
	  printf "Backfill now uses a single shared dataset version: %s\n" "$(BACKFILL_DATASET_VERSION)"; \
	  printf "Custom backfill dataset versions are disabled.\n"; \
	  exit 1; \
	fi; \
	printf "Deleting manual backfill jobs for dataset %s in %s\n" "$$dataset_version" "$(SIPP_NAMESPACE)"; \
	oc delete jobs -n "$(SIPP_NAMESPACE)" -l "app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=$$dataset_version" --ignore-not-found
