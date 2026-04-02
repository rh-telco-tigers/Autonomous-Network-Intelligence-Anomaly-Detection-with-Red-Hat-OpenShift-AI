SHELL := /bin/zsh

INCIDENT_RELEASE_NAMESPACE ?= ims-demo-lab
INCIDENT_RELEASE_DATASET_VERSION ?=
INCIDENT_RELEASE_DEFAULT_DATASET_PREFIX ?= backfill-sipp-100k
INCIDENT_RELEASE_BACKFILL_PATH := k8s/manual/traffic-backfill-100k
INCIDENT_RELEASE_BACKFILL_RENDERER := $(INCIDENT_RELEASE_BACKFILL_PATH)/render_jobs.py
PIPELINE_NAMESPACE ?= ims-demo-lab
PIPELINE_NAME ?= ims-demo-container-build
PIPELINE_SERVICE_ACCOUNT ?= pipeline
PIPELINE_GIT_URL ?= http://gitea-http.gitea.svc.cluster.local:3000/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git
PIPELINE_GIT_REVISION ?= main
PIPELINE_WORKSPACE_SIZE ?= 1Gi
KFP_NAMESPACE ?= ims-demo-lab
KFP_DSPA_NAME ?= dspa
KFP_BOOTSTRAP_SERVICE_ACCOUNT ?= ims-kfp-bootstrap
KFP_BOOTSTRAP_IMAGE ?= registry.access.redhat.com/ubi9/python-311:latest
KFP_ANOMALY_CONFIGMAP ?= ims-kfp-assets
KFP_ANOMALY_PACKAGE_PATH ?= /opt/kfp/ims_anomaly_pipeline.yaml
KFP_ANOMALY_PIPELINE_NAME ?= ims-anomaly-platform-train-and-register
KFP_ANOMALY_EXPERIMENT_NAME ?= ims-demo
KFP_ANOMALY_RUN_NAME ?= ims-anomaly-platform-manual-$(shell date +%Y%m%d-%H%M%S)
KFP_ANOMALY_PARAMETERS_JSON ?= {"dataset_version":"live-sipp-v1","baseline_version":"baseline-v1","automl_version":"candidate-v1","automl_engine":"autogluon"}
KFP_FEATURE_BUNDLE_CONFIGMAP ?= ims-feature-bundle-kfp-assets
KFP_FEATURE_BUNDLE_PACKAGE_PATH ?= /opt/kfp/ims_feature_bundle_pipeline.yaml
KFP_FEATURE_BUNDLE_PIPELINE_NAME ?= ims-feature-bundle-publish
KFP_FEATURE_BUNDLE_EXPERIMENT_NAME ?= ims-featurestore
KFP_FEATURE_BUNDLE_RUN_NAME ?= ims-feature-bundle-manual-$(shell date +%Y%m%d-%H%M%S)
KFP_FEATURE_BUNDLE_PARAMETERS_JSON ?= {"bundle_version":"ims-feature-bundle-v1","source_dataset_versions_json":"[\"live-sipp-v1\"]","project":"ims-demo"}
KFP_FEATURESTORE_CONFIGMAP ?= ims-featurestore-kfp-assets
KFP_FEATURESTORE_PACKAGE_PATH ?= /opt/kfp/ims_featurestore_pipeline.yaml
KFP_FEATURESTORE_PIPELINE_NAME ?= ims-featurestore-train-and-register
KFP_FEATURESTORE_EXPERIMENT_NAME ?= ims-featurestore
KFP_FEATURESTORE_RUN_NAME ?= ims-featurestore-manual-$(shell date +%Y%m%d-%H%M%S)
KFP_FEATURESTORE_PARAMETERS_JSON ?= {"bundle_version":"ims-feature-bundle-v1","feature_service_name":"ims_anomaly_scoring_v1","baseline_version":"baseline-fs-v1","candidate_version":"candidate-fs-v1","automl_engine":"autogluon","model_name":"ims-anomaly-featurestore","model_version_name":"ims-anomaly-featurestore-v1","serving_model_name":"ims-predictive-fs","serving_runtime_name":"nvidia-triton-runtime","serving_prefix":"predictive-featurestore","serving_alias":"current"}
FEATURESTORE_SMOKE_IMAGE ?= image-registry.openshift-image-registry.svc:5000/ims-demo-lab/ims-ai-featurestore-trainer:latest
CURRENT_PREDICTIVE_ENDPOINT ?= http://ims-predictive-predictor.ims-demo-lab.svc.cluster.local:8080
CURRENT_PREDICTIVE_MODEL_NAME ?= ims-predictive
FEATURESTORE_PREDICTIVE_ENDPOINT ?= http://ims-predictive-fs-predictor.ims-demo-lab.svc.cluster.local:8080
FEATURESTORE_PREDICTIVE_MODEL_NAME ?= ims-predictive-fs
FEATURESTORE_SMOKE_MAX_SCORE_DELTA ?= -1
FEATURESTORE_SMOKE_RUN_NAME ?= ims-featurestore-serving-smoke-$(shell date +%Y%m%d-%H%M%S)

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

trigger-build-pipeline: ## Start the Tekton image build pipeline after pushing to Gitea
	@printf "Triggering %s in %s for %s @ %s\n" "$(PIPELINE_NAME)" "$(PIPELINE_NAMESPACE)" "$(PIPELINE_GIT_URL)" "$(PIPELINE_GIT_REVISION)"
	@printf '%s\n' \
	  'apiVersion: tekton.dev/v1' \
	  'kind: PipelineRun' \
	  'metadata:' \
	  '  generateName: ims-demo-build-' \
	  '  namespace: $(PIPELINE_NAMESPACE)' \
	  'spec:' \
	  '  pipelineRef:' \
	  '    name: $(PIPELINE_NAME)' \
	  '  params:' \
	  '    - name: git-url' \
	  '      value: $(PIPELINE_GIT_URL)' \
	  '    - name: git-revision' \
	  '      value: $(PIPELINE_GIT_REVISION)' \
	  '  taskRunTemplate:' \
	  '    serviceAccountName: $(PIPELINE_SERVICE_ACCOUNT)' \
	  '  workspaces:' \
	  '    - name: source' \
	  '      volumeClaimTemplate:' \
	  '        spec:' \
	  '          accessModes:' \
	  '            - ReadWriteOnce' \
	  '          resources:' \
	  '            requests:' \
	  '              storage: $(PIPELINE_WORKSPACE_SIZE)' \
	| oc create -f -

trigger-anomaly-platform-pipeline: ## Start a fresh KFP ims-anomaly-platform-train-and-register run
	@printf "Triggering KFP %s in %s as %s\n" "$(KFP_ANOMALY_PIPELINE_NAME)" "$(KFP_NAMESPACE)" "$(KFP_ANOMALY_RUN_NAME)"
	@printf '%s\n' \
	  'apiVersion: batch/v1' \
	  'kind: Job' \
	  'metadata:' \
	  '  generateName: ims-kfp-manual-' \
	  '  namespace: $(KFP_NAMESPACE)' \
	  'spec:' \
	  '  backoffLimit: 6' \
	  '  ttlSecondsAfterFinished: 1800' \
	  '  template:' \
	  '    metadata:' \
	  '      labels:' \
	  '        app: ims-kfp-manual' \
	  '    spec:' \
	  '      serviceAccountName: $(KFP_BOOTSTRAP_SERVICE_ACCOUNT)' \
	  '      restartPolicy: OnFailure' \
	  '      containers:' \
	  '        - name: publish-pipeline' \
	  '          image: $(KFP_BOOTSTRAP_IMAGE)' \
	  '          env:' \
	  '            - name: HOME' \
	  '              value: /tmp' \
	  '            - name: POD_NAMESPACE' \
	  '              valueFrom:' \
	  '                fieldRef:' \
	  '                  fieldPath: metadata.namespace' \
	  '            - name: DSPA_NAME' \
	  '              value: $(KFP_DSPA_NAME)' \
	  '            - name: PIPELINE_PACKAGE_PATH' \
	  '              value: $(KFP_ANOMALY_PACKAGE_PATH)' \
	  '            - name: PIPELINE_NAME' \
	  '              value: $(KFP_ANOMALY_PIPELINE_NAME)' \
	  '            - name: EXPERIMENT_NAME' \
	  '              value: $(KFP_ANOMALY_EXPERIMENT_NAME)' \
	  '            - name: RUN_NAME' \
	  '              value: $(KFP_ANOMALY_RUN_NAME)' \
	  '            - name: PIPELINE_PARAMETERS_JSON' \
	  '              value: |' \
	  '                $(KFP_ANOMALY_PARAMETERS_JSON)' \
	  '          command:' \
	  '            - /bin/bash' \
	  '            - -lc' \
	  '          args:' \
	  '            - |' \
	  '              python -m pip install --no-cache-dir --target /tmp/kfp-site kfp==2.8.0' \
	  '              export PYTHONPATH="/tmp/kfp-site:$${PYTHONPATH}"' \
	  '              python /opt/kfp/publish_pipeline.py' \
	  '          volumeMounts:' \
	  '            - name: kfp-assets' \
	  '              mountPath: /opt/kfp' \
	  '              readOnly: true' \
	  '          resources:' \
	  '            requests:' \
	  '              cpu: 100m' \
	  '              memory: 256Mi' \
	  '            limits:' \
	  '              memory: 512Mi' \
	  '      volumes:' \
	  '        - name: kfp-assets' \
	  '          configMap:' \
	  '            name: $(KFP_ANOMALY_CONFIGMAP)' \
	| oc create -f -

trigger-feature-bundle-pipeline: ## Start a fresh KFP ims-feature-bundle-publish run
	@printf "Triggering KFP %s in %s as %s\n" "$(KFP_FEATURE_BUNDLE_PIPELINE_NAME)" "$(KFP_NAMESPACE)" "$(KFP_FEATURE_BUNDLE_RUN_NAME)"
	@printf '%s\n' \
	  'apiVersion: batch/v1' \
	  'kind: Job' \
	  'metadata:' \
	  '  generateName: ims-feature-bundle-kfp-manual-' \
	  '  namespace: $(KFP_NAMESPACE)' \
	  'spec:' \
	  '  backoffLimit: 6' \
	  '  ttlSecondsAfterFinished: 1800' \
	  '  template:' \
	  '    metadata:' \
	  '      labels:' \
	  '        app: ims-feature-bundle-kfp-manual' \
	  '    spec:' \
	  '      serviceAccountName: $(KFP_BOOTSTRAP_SERVICE_ACCOUNT)' \
	  '      restartPolicy: OnFailure' \
	  '      containers:' \
	  '        - name: publish-pipeline' \
	  '          image: $(KFP_BOOTSTRAP_IMAGE)' \
	  '          env:' \
	  '            - name: HOME' \
	  '              value: /tmp' \
	  '            - name: POD_NAMESPACE' \
	  '              valueFrom:' \
	  '                fieldRef:' \
	  '                  fieldPath: metadata.namespace' \
	  '            - name: DSPA_NAME' \
	  '              value: $(KFP_DSPA_NAME)' \
	  '            - name: PIPELINE_PACKAGE_PATH' \
	  '              value: $(KFP_FEATURE_BUNDLE_PACKAGE_PATH)' \
	  '            - name: PIPELINE_NAME' \
	  '              value: $(KFP_FEATURE_BUNDLE_PIPELINE_NAME)' \
	  '            - name: EXPERIMENT_NAME' \
	  '              value: $(KFP_FEATURE_BUNDLE_EXPERIMENT_NAME)' \
	  '            - name: RUN_NAME' \
	  '              value: $(KFP_FEATURE_BUNDLE_RUN_NAME)' \
	  '            - name: PIPELINE_PARAMETERS_JSON' \
	  '              value: |' \
	  '                $(KFP_FEATURE_BUNDLE_PARAMETERS_JSON)' \
	  '          command:' \
	  '            - /bin/bash' \
	  '            - -lc' \
	  '          args:' \
	  '            - |' \
	  '              python -m pip install --no-cache-dir --target /tmp/kfp-site kfp==2.8.0' \
	  '              export PYTHONPATH="/tmp/kfp-site:$${PYTHONPATH}"' \
	  '              python /opt/kfp/publish_feature_bundle_pipeline.py' \
	  '          volumeMounts:' \
	  '            - name: kfp-assets' \
	  '              mountPath: /opt/kfp' \
	  '              readOnly: true' \
	  '          resources:' \
	  '            requests:' \
	  '              cpu: 100m' \
	  '              memory: 256Mi' \
	  '            limits:' \
	  '              memory: 512Mi' \
	  '      volumes:' \
	  '        - name: kfp-assets' \
	  '          configMap:' \
	  '            name: $(KFP_FEATURE_BUNDLE_CONFIGMAP)' \
	| oc create -f -

trigger-featurestore-pipeline: ## Start a fresh KFP ims-featurestore-train-and-register run
	@printf "Triggering KFP %s in %s as %s\n" "$(KFP_FEATURESTORE_PIPELINE_NAME)" "$(KFP_NAMESPACE)" "$(KFP_FEATURESTORE_RUN_NAME)"
	@printf '%s\n' \
	  'apiVersion: batch/v1' \
	  'kind: Job' \
	  'metadata:' \
	  '  generateName: ims-featurestore-kfp-manual-' \
	  '  namespace: $(KFP_NAMESPACE)' \
	  'spec:' \
	  '  backoffLimit: 6' \
	  '  ttlSecondsAfterFinished: 1800' \
	  '  template:' \
	  '    metadata:' \
	  '      labels:' \
	  '        app: ims-featurestore-kfp-manual' \
	  '    spec:' \
	  '      serviceAccountName: $(KFP_BOOTSTRAP_SERVICE_ACCOUNT)' \
	  '      restartPolicy: OnFailure' \
	  '      containers:' \
	  '        - name: publish-pipeline' \
	  '          image: $(KFP_BOOTSTRAP_IMAGE)' \
	  '          env:' \
	  '            - name: HOME' \
	  '              value: /tmp' \
	  '            - name: POD_NAMESPACE' \
	  '              valueFrom:' \
	  '                fieldRef:' \
	  '                  fieldPath: metadata.namespace' \
	  '            - name: DSPA_NAME' \
	  '              value: $(KFP_DSPA_NAME)' \
	  '            - name: PIPELINE_PACKAGE_PATH' \
	  '              value: $(KFP_FEATURESTORE_PACKAGE_PATH)' \
	  '            - name: PIPELINE_NAME' \
	  '              value: $(KFP_FEATURESTORE_PIPELINE_NAME)' \
	  '            - name: EXPERIMENT_NAME' \
	  '              value: $(KFP_FEATURESTORE_EXPERIMENT_NAME)' \
	  '            - name: RUN_NAME' \
	  '              value: $(KFP_FEATURESTORE_RUN_NAME)' \
	  '            - name: PIPELINE_PARAMETERS_JSON' \
	  '              value: |' \
	  '                $(KFP_FEATURESTORE_PARAMETERS_JSON)' \
	  '          command:' \
	  '            - /bin/bash' \
	  '            - -lc' \
	  '          args:' \
	  '            - |' \
	  '              python -m pip install --no-cache-dir --target /tmp/kfp-site kfp==2.8.0' \
	  '              export PYTHONPATH="/tmp/kfp-site:$${PYTHONPATH}"' \
	  '              python /opt/kfp/publish_featurestore_pipeline.py' \
	  '          volumeMounts:' \
	  '            - name: kfp-assets' \
	  '              mountPath: /opt/kfp' \
	  '              readOnly: true' \
	  '          resources:' \
	  '            requests:' \
	  '              cpu: 100m' \
	  '              memory: 256Mi' \
	  '            limits:' \
	  '              memory: 512Mi' \
	  '      volumes:' \
	  '        - name: kfp-assets' \
	  '          configMap:' \
	  '            name: $(KFP_FEATURESTORE_CONFIGMAP)' \
	| oc create -f -

smoke-check-featurestore-serving: ## Run an in-cluster smoke comparison for current and feature-store serving
	@printf "Triggering serving smoke check in %s as %s\n" "$(KFP_NAMESPACE)" "$(FEATURESTORE_SMOKE_RUN_NAME)"
	@printf '%s\n' \
	  'apiVersion: batch/v1' \
	  'kind: Job' \
	  'metadata:' \
	  '  generateName: ims-featurestore-serving-smoke-' \
	  '  namespace: $(KFP_NAMESPACE)' \
	  'spec:' \
	  '  backoffLimit: 1' \
	  '  ttlSecondsAfterFinished: 1800' \
	  '  template:' \
	  '    metadata:' \
	  '      labels:' \
	  '        app: ims-featurestore-serving-smoke' \
	  '    spec:' \
	  '      restartPolicy: Never' \
	  '      containers:' \
	  '        - name: smoke-check' \
	  '          image: $(FEATURESTORE_SMOKE_IMAGE)' \
	  '          command:' \
	  '            - python' \
	  '            - ai/training/serving_smoke_check.py' \
	  '            - --current-endpoint' \
	  '            - $(CURRENT_PREDICTIVE_ENDPOINT)' \
	  '            - --current-model-name' \
	  '            - $(CURRENT_PREDICTIVE_MODEL_NAME)' \
	  '            - --featurestore-endpoint' \
	  '            - $(FEATURESTORE_PREDICTIVE_ENDPOINT)' \
	  '            - --featurestore-model-name' \
	  '            - $(FEATURESTORE_PREDICTIVE_MODEL_NAME)' \
	  '            - --max-score-delta' \
	  '            - "$(FEATURESTORE_SMOKE_MAX_SCORE_DELTA)"' \
	  '          resources:' \
	  '            requests:' \
	  '              cpu: 100m' \
	  '              memory: 256Mi' \
	  '            limits:' \
	  '              memory: 512Mi' \
	| oc create -f -

trigger-incident-release: ## Start a fresh manual 100k backfill dataset
	@dataset_version="$${INCIDENT_RELEASE_DATASET_VERSION:-$(INCIDENT_RELEASE_DEFAULT_DATASET_PREFIX)-$$(date +%Y%m%d-%H%M%S)}"; \
	printf "Creating manual backfill jobs for dataset %s in %s\n" "$$dataset_version" "$(INCIDENT_RELEASE_NAMESPACE)"; \
	kustomize build "$(INCIDENT_RELEASE_BACKFILL_PATH)" \
	  | python3 "$(INCIDENT_RELEASE_BACKFILL_RENDERER)" --dataset-version "$$dataset_version" \
	  | oc create -f -; \
	printf "Watch jobs: oc get jobs -n %s -l app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=%s\n" "$(INCIDENT_RELEASE_NAMESPACE)" "$$dataset_version"; \
	printf "Watch pods: oc get pods -n %s -l app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=%s\n" "$(INCIDENT_RELEASE_NAMESPACE)" "$$dataset_version"; \
	printf "Stop run: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=%s\n" "$$dataset_version"

stop-incident-release: ## Stop and delete one backfill dataset version
	@[ -n "$(INCIDENT_RELEASE_DATASET_VERSION)" ] || { \
	  printf "Set INCIDENT_RELEASE_DATASET_VERSION, for example: make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=%s-20260401-120000\n" "$(INCIDENT_RELEASE_DEFAULT_DATASET_PREFIX)"; \
	  exit 1; \
	}
	@printf "Deleting manual backfill jobs for dataset %s in %s\n" "$(INCIDENT_RELEASE_DATASET_VERSION)" "$(INCIDENT_RELEASE_NAMESPACE)"
	oc delete jobs -n "$(INCIDENT_RELEASE_NAMESPACE)" -l "app.kubernetes.io/part-of=sipp-backfill-100k,ims.redhat.com/backfill-dataset-version=$(INCIDENT_RELEASE_DATASET_VERSION)" --ignore-not-found

