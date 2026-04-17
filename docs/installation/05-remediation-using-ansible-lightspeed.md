# 05 — Remediation Using Ansible Lightspeed

This guide walks through enabling the AI-driven remediation workflow, which uses Ansible Lightspeed to generate and execute remediation playbooks in response to IMS anomaly events detected by the EDA Kafka pipeline.

---

## Prerequisites

- The platform has been deployed via the bootstrap (see [02-installation.md](02-installation.md))
- The `ani-aap` application is `Synced` and `Healthy`
- You have `oc` CLI access with cluster-admin permissions
- You understand that the Lightspeed API token is still a manual step on the current branch

---

## Step 1 — Configure the Lightspeed Backend Secret

The repository contains the desired Lightspeed backend secret at `k8s/base/aap-platform/ansible-lightspeed-secret.yaml`. Populate it with your LLM backend details and make sure the cluster has a matching `lightspeed-secret` in the `aap` namespace.

1. Get the Gitea URL:
   ```bash
   oc get route gitea -n gitea -o jsonpath='{.spec.host}'
   ```

2. Open the Gitea UI in your browser and navigate to:
   ```
   IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI
     → k8s → base → aap-platform → ansible-lightspeed-secret.yaml
   ```

3. Click the **pencil (edit) icon** and replace the placeholder values with your LLM backend details:

   ```yaml
   stringData:
     chatbot_model: granite-3-2-8b-instruct  # update if using a different model
     chatbot_url: <url>                       # replace with your LLM endpoint URL
     chatbot_token: <token>                   # replace with your LLM API token
   ```

4. Scroll down, add a commit message (for example `Configure Lightspeed LLM backend`), and click **Commit Changes**.

5. If the secret does not already exist in the cluster, apply it manually:

```bash
oc apply -f k8s/base/aap-platform/ansible-lightspeed-secret.yaml -n aap
```

Confirm it has been applied:

```bash
oc get secret lightspeed-secret -n aap -o jsonpath='{.data.chatbot_url}' | base64 -d && echo
```

---

## Step 2 — Roll Out the Ansible Lightspeed Pods

Restart the Lightspeed deployments to pick up the updated secret:

```bash
oc rollout restart deployment/ansible-lightspeed -n aap
oc rollout restart deployment/ansible-lightspeed-api -n aap
```

---

## Step 3 — Verify the Pods are Running

Wait for both deployments to complete their rollout:

```bash
oc rollout status deployment/ansible-lightspeed -n aap
oc rollout status deployment/ansible-lightspeed-api -n aap
```

Confirm all pods are in `Running` state:

```bash
oc get pods -n aap | grep lightspeed
```

Both deployments should show `1/1 Running` before continuing.

---

## Step 4 — Create an Ansible Lightspeed API Token

### 4.1 — Find the Lightspeed Route

Retrieve the Ansible Lightspeed route from the `aap` namespace:

```bash
oc get route -n aap | grep lightspeed
```

Note the hostname — referred to as `<lightspeed_route>` in the steps below.

### 4.2 — Access the Admin Portal

Open the Django administration portal in your browser:

```
https://<lightspeed_route>/admin
```

### 4.3 — Log In as Administrator

Use the following credentials:

| Field | Value |
|-------|-------|
| **Username** | `admin` |
| **Password** | The value of the secret named `<lightspeed-custom-resource-name>-admin-password` in the `aap` namespace |

Retrieve the password with:

```bash
oc get secret -n aap | grep lightspeed | grep admin-password
oc get secret <lightspeed-custom-resource-name>-admin-password -n aap \
  -o jsonpath='{.data.password}' | base64 -d && echo
```

### 4.4 — Verify the Platform User

1. On the **Django administration** window, select **Users** from the **Users** area.
2. Confirm that the `admin` user is listed.

### 4.5 — Create an Access Token

1. From the **Django OAuth toolkit** area, select **Access tokens → Add**.
2. Fill in the following fields and click **Save**:

| Field | Value |
|-------|-------|
| **User** | Use the magnifying glass icon to search and select the admin |
| **Token** | Specify "redhat" |
| **Token checksum** | Specify "redhat" |
| **Id token** | Leave as it is |
| **Application** | Select **Ansible Lightspeed for VS Code** |
| **Expires** | Set the desired expiry date and time |
| **Scope** | `read write` |

3. Copy and securely store the token value before saving.

### 4.6 — Store the Token in the Secret Expected by the Remediation Job

The remediation bootstrap Job `aap-controller-lightspeed-template-config` reads the token from the secret `aap-lightspeed-chatbot-api-key` in the `aap` namespace.

Create or update that secret with the token you just copied:

```bash
oc create secret generic aap-lightspeed-chatbot-api-key -n aap \
  --from-literal=api_key='<copied_token>' \
  --dry-run=client -o yaml | oc apply -f -
```

Verify it exists:

```bash
oc get secret aap-lightspeed-chatbot-api-key -n aap
```

If `ani-remediation` or `aap-controller-lightspeed-template-config` was already stuck before the secret existed, force one new reconcile:

```bash
oc delete pod -n aap -l job-name=aap-controller-lightspeed-template-config --ignore-not-found
oc annotate application.argoproj.io/ani-remediation -n openshift-gitops argocd.argoproj.io/refresh=hard --overwrite
```

---

## Verification

To confirm the end-to-end remediation flow is active, check that the EDA Kafka activation is running:

```bash
# Confirm the token-backed template config job is no longer blocked
oc get job aap-controller-lightspeed-template-config -n aap

# Verify EDA Kafka bootstrap job completed successfully
oc get job eda-kafka-bootstrap -n aap

# Check the ANI Remediation activation is enabled in EDA
oc get pods -n aap | grep eda

# Confirm the ArgoCD remediation slice is no longer degraded
oc get application.argoproj.io ani-remediation -n openshift-gitops
```

The EDA activation **ANI Remediation** should be listening on the `aiops-ansible-playbook-generate-instruction` Kafka topic. When an anomaly event is detected, it will automatically trigger the **ANI Remediation - Lightspeed Playbook Generator** job template in AAP Controller and generate playbook.
