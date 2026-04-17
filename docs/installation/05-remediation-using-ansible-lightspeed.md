# 05 — Remediation Using Ansible Lightspeed

This guide walks through enabling the AI-driven remediation workflow, which uses Ansible Lightspeed to generate and execute remediation playbooks in response to IMS anomaly events detected by the EDA Kafka pipeline.

---

## Prerequisites

- The platform has been deployed through the GitOps path in [02-installation.md](02-installation.md)
- AAP is installed and the AAP license import is complete
- You have `oc` CLI access with cluster-admin permissions
- You understand that the Lightspeed secrets are still a manual step on the current branch
- Do not commit real secret values to Git or store them in Gitea

---

## Step 1 — Create the Manual Lightspeed Backend Secret

Create the backend secret directly in the cluster. The integration expects this exact secret name:

- `name: lightspeed-secret`
- `namespace: aap`

Create or update it with your real backend values:

```bash
oc create secret generic lightspeed-secret -n aap \
  --from-literal=chatbot_model='<model_name>' \
  --from-literal=chatbot_url='https://<lightspeed-backend>/v1' \
  --from-literal=chatbot_token='<backend_token>' \
  --dry-run=client -o yaml | oc apply -f -
```

Verify the values are present without printing the token:

```bash
oc get secret lightspeed-secret -n aap
oc get secret lightspeed-secret -n aap -o jsonpath='{.data.chatbot_url}' | base64 -d && echo
oc get secret lightspeed-secret -n aap -o jsonpath='{.data.chatbot_model}' | base64 -d && echo
```

---

## Step 2 — Verify the Lightspeed Workloads Are Running

```bash
oc get deploy -n aap | rg 'lightspeed'
oc get pods -n aap | rg 'lightspeed'
oc get route -n aap | rg 'lightspeed'
```

Continue when:

- `aap-lightspeed-api` is available
- `aap-lightspeed-chatbot-api` is available
- the related pods are `Running`
- route `aap-lightspeed` is admitted

If you changed `lightspeed-secret` after those deployments were already running, restart them once:

```bash
oc rollout restart deployment/aap-lightspeed-api -n aap
oc rollout restart deployment/aap-lightspeed-chatbot-api -n aap
```

---

## Step 3 — Create an Ansible Lightspeed API Token

### 3.1 — Find the Lightspeed Route

Retrieve the Ansible Lightspeed route from the `aap` namespace:

```bash
oc get route -n aap | grep lightspeed
```

Note the hostname — referred to as `<lightspeed_route>` in the steps below.

### 3.2 — Access the Admin Portal

Open the Django administration portal in your browser:

```
https://<lightspeed_route>/admin
```

### 3.3 — Log In as Administrator

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

### 3.4 — Verify the Platform User

1. On the **Django administration** window, select **Users** from the **Users** area.
2. Confirm that the `admin` user is listed.

### 3.5 — Create an Access Token

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

## Step 4 — Create the Manual Token Secret for the Remediation Job

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

## Step 5 — Rerun the Remediation Reconcile

After both manual secrets exist, force one new reconcile so the bootstrap Jobs can create the AAP connection secret, the Lightspeed job template configuration, and the EDA Kafka activation:

```bash
oc delete secret aap-controller-connection -n aap --ignore-not-found
oc delete job -n aap \
  ani-remediation-connection-secret \
  aap-controller-project-readiness \
  aap-controller-job-template-readiness \
  aap-controller-lightspeed-template-config \
  eda-kafka-bootstrap \
  --ignore-not-found
oc annotate application.argoproj.io/ani-remediation -n openshift-gitops argocd.argoproj.io/refresh=hard --overwrite
```

---

## Verification

To confirm the end-to-end remediation flow is active, verify the bootstrap outputs directly:

```bash
oc get secret aap-controller-connection -n aap
oc get job -n aap \
  ani-remediation-connection-secret \
  aap-controller-project-readiness \
  aap-controller-job-template-readiness \
  aap-controller-lightspeed-template-config \
  eda-kafka-bootstrap
oc get application.argoproj.io ani-remediation -n openshift-gitops
```

Check the AAP Controller template:

```bash
CONTROLLER_HOST="$(oc get route aap-controller -n aap -o jsonpath='{.status.ingress[0].host}')"
CONTROLLER_PASS="$(oc extract -n aap secret/aap-controller-admin-password --to=- --keys=password 2>/dev/null | tail -n1)"
curl -ksu "admin:${CONTROLLER_PASS}" \
  "https://${CONTROLLER_HOST}/api/controller/v2/job_templates/?page_size=100&name=IMS%20Remediation%20-%20Lightspeed%20Playbook%20Generator" \
  | python3 -m json.tool
```

Expected result:

- the result count is `1`

Check the EDA activation:

```bash
EDA_HOST="$(oc get route aap-eda -n aap -o jsonpath='{.status.ingress[0].host}')"
EDA_PASS="$(oc extract -n aap secret/aap-eda-admin-password --to=- --keys=password 2>/dev/null | tail -n1)"
curl -ksu "admin:${EDA_PASS}" \
  "https://${EDA_HOST}/api/eda/v1/activations/?page_size=100" \
  | python3 -m json.tool
```

Expected result:

- activation `ANI Remediation` exists
- its `rulebook_name` is `generate-playbook-event.yml`
- its `status` is `running`

When those checks pass, the EDA activation is listening on Kafka topic `aiops-ansible-playbook-generate-instruction` and launches AAP template `IMS Remediation - Lightspeed Playbook Generator` when the control-plane publishes a playbook-generation request.
