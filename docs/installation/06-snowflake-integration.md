# Snowflake Integration

This document explains how the ANI Feature Store is configured to use Snowflake as an online store, and provides steps to run the demo.

## Overview

The ANI demo includes a second Feature Store instance — `ani-featurestore-snowflake` — that demonstrates the capability of the Red Hat OpenShift AI Feast operator to connect to Snowflake as an online store backend.

The main `ani-featurestore` (SQLite online store) is **untouched**. This is a standalone demo that runs alongside it.

### What gets stored in Snowflake

Only the feature view used for online inference is materialized into Snowflake:

| Feature View | Snowflake Table | Purpose |
|---|---|---|
| `ani_window_numeric_v1` | `ANI_WINDOW_NUMERIC_V1` | 9 numeric SIP features used by `ani_anomaly_scoring_v1` for inference |

Context and label views are training-only and are not written to the online store.

### Architecture

```
S3/Minio (Parquet)
       |
       | feast materialize-incremental (CronJob, every 2 hours)
       |
Snowflake: ANI_FEAST_DEMO.FEAST_ONLINE.ANI_WINDOW_NUMERIC_V1
       |
       | get_online_features()
       |
ani-featurestore-snowflake online store server
```

## Files

| File | Purpose |
|---|---|
| `k8s/base/feature-store/featurestore-snowflake-demo.yaml` | FeatureStore CR — Feast operator instance with Snowflake online store |
| `k8s/base/feature-store/snowflake-secret.yaml` | Kubernetes Secret with Snowflake credentials (do not commit) |
| `k8s/base/feature-store/snowflake-materialize-cronjob.yaml` | CronJob that runs `feast materialize-incremental` every 2 hours |

## Prerequisites

1. A Snowflake account (trial or full)
2. Access to the OpenShift cluster with `oc` CLI

## Step 1 — Snowflake Setup

Log in to your Snowflake account and run the following in a Worksheet:

```sql
CREATE DATABASE ANI_FEAST_DEMO;
CREATE WAREHOUSE ANI_FEAST_WH WITH WAREHOUSE_SIZE = 'X-SMALL' AUTO_SUSPEND = 60;
CREATE SCHEMA ANI_FEAST_DEMO.FEAST_ONLINE;
```

To find your **account identifier**, go to **Admin → Accounts** in the Snowflake UI. It looks like `abc12345.us-east-1` or `orgname-accountname`.

## Step 2 — Create the Kubernetes Secret

Create `k8s/base/feature-store/snowflake-secret.yaml` with your values:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: snowflake-feast-credentials
  namespace: ani-demo-lab
type: Opaque
stringData:
  SNOWFLAKE_ACCOUNT: <your-account-identifier>    # e.g. abc12345.us-east-1
  SNOWFLAKE_USER: <your-username>
  SNOWFLAKE_PASSWORD: <your-password>
  SNOWFLAKE_ROLE: ACCOUNTADMIN
  SNOWFLAKE_WAREHOUSE: ANI_FEAST_WH
  SNOWFLAKE_DATABASE: ANI_FEAST_DEMO
  SNOWFLAKE_SCHEMA: FEAST_ONLINE
```

> **Important:** Do not commit this file to git. Add it to `.gitignore`:
> ```
> k8s/base/feature-store/snowflake-secret.yaml
> ```

## Step 3 — Deploy

Apply the secret and the FeatureStore CR:

```bash
oc apply -f k8s/base/feature-store/snowflake-secret.yaml
oc apply -f k8s/base/feature-store/featurestore-snowflake-demo.yaml
oc apply -f k8s/base/feature-store/snowflake-materialize-cronjob.yaml
```

Wait for the Feast operator to start the pods:

```bash
oc get pods -n ani-demo-lab | grep snowflake
```

## Step 4 — Run Initial Materialization

The CronJob runs every 2 hours automatically. To trigger it immediately for the first time:

```bash
oc create job --from=cronjob/ani-snowflake-materialize ani-snowflake-materialize-init -n ani-datascience
```

Watch the logs:

```bash
oc logs -f job/ani-snowflake-materialize-init -n ani-datascience
```

A successful run ends with:
```
=== Done — Snowflake tables updated ===
```

## Step 5 — Verify in Snowflake UI

In the Snowflake UI, navigate to:

**Data → Databases → ANI_FEAST_DEMO → FEAST_ONLINE → Tables**

You will see `ANI_WINDOW_NUMERIC_V1` populated with rows. Each row represents one feature window with the 9 numeric SIP features:

```sql
SELECT * FROM ANI_FEAST_DEMO.FEAST_ONLINE.ANI_WINDOW_NUMERIC_V1 LIMIT 10;
```

## How the FeatureStore CR configures Snowflake

The `featurestore-snowflake-demo.yaml` CR tells the OpenShift AI Feast operator to use Snowflake as the online store backend instead of the default SQLite:

```yaml
services:
  onlineStore:
    snowflake:
      account: <account-identifier>
      database: ANI_FEAST_DEMO
      schema: FEAST_ONLINE
      warehouse: ANI_FEAST_WH
      role: ACCOUNTADMIN
      credentialsSecret:
        name: snowflake-feast-credentials   # user + password from Secret
```
