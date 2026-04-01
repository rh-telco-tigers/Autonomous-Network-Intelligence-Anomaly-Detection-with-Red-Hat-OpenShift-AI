SHELL := /bin/zsh

INCIDENT_RELEASE_BACKFILL_PATH := k8s/manual/traffic-backfill-100k
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

.PHONY: help kustomize-demo validate-python repo-tree trigger-build-pipeline trigger-anomaly-platform-pipeline trigger-incident-release stop-incident-release

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

trigger-incident-release: ## Start the manual 100k incident-release backfill jobs
	oc apply -k $(INCIDENT_RELEASE_BACKFILL_PATH)

stop-incident-release: ## Stop and delete the manual 100k incident-release backfill jobs
	oc delete -k $(INCIDENT_RELEASE_BACKFILL_PATH) --ignore-not-found

