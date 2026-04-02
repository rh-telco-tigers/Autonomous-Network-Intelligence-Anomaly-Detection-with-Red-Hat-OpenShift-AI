import os
from datetime import timedelta

from feast import Field, FeatureView, FileSource
from feast.data_format import ParquetFormat
from feast.types import Float32, Int64, String

from entities import feature_window


OFFLINE_SOURCE_PATH = os.getenv(
    "IMS_FEATURESTORE_OFFLINE_SOURCE_PATH",
    "file:///workspace/feature-bundles/latest/feature_store/offline_source.parquet",
)
LABEL_SOURCE_PATH = os.getenv(
    "IMS_FEATURESTORE_LABEL_SOURCE_PATH",
    "file:///workspace/feature-bundles/latest/parquet/window_labels.parquet",
)
S3_ENDPOINT_OVERRIDE = os.getenv("FEAST_S3_ENDPOINT_URL", "").strip() or None

offline_source = FileSource(
    name="ims_feature_bundle_offline_source",
    path=OFFLINE_SOURCE_PATH,
    s3_endpoint_override=S3_ENDPOINT_OVERRIDE,
    file_format=ParquetFormat(),
    timestamp_field="event_timestamp",
    created_timestamp_column="created_timestamp",
)

label_source = FileSource(
    name="ims_feature_bundle_label_source",
    path=LABEL_SOURCE_PATH,
    s3_endpoint_override=S3_ENDPOINT_OVERRIDE,
    file_format=ParquetFormat(),
    timestamp_field="event_timestamp",
    created_timestamp_column="created_timestamp",
)

ims_window_numeric_v1 = FeatureView(
    name="ims_window_numeric_v1",
    entities=[feature_window],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="register_rate", dtype=Float32),
        Field(name="invite_rate", dtype=Float32),
        Field(name="bye_rate", dtype=Float32),
        Field(name="error_4xx_ratio", dtype=Float32),
        Field(name="error_5xx_ratio", dtype=Float32),
        Field(name="latency_p95", dtype=Float32),
        Field(name="retransmission_count", dtype=Float32),
        Field(name="inter_arrival_mean", dtype=Float32),
        Field(name="payload_variance", dtype=Float32),
    ],
    source=offline_source,
    tags={"domain": "ims", "contract": "scoring", "version": "v1"},
)

ims_window_context_v1 = FeatureView(
    name="ims_window_context_v1",
    entities=[feature_window],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="dataset_version", dtype=String),
        Field(name="source_snapshot_id", dtype=String),
        Field(name="scenario_name", dtype=String),
        Field(name="source", dtype=String),
        Field(name="feature_source", dtype=String),
        Field(name="transport", dtype=String),
        Field(name="call_limit", dtype=Int64),
        Field(name="rate", dtype=Float32),
        Field(name="approval_status", dtype=String),
        Field(name="rca_status", dtype=String),
    ],
    source=offline_source,
    tags={"domain": "ims", "contract": "context", "version": "v1"},
)

ims_training_label_v1 = FeatureView(
    name="ims_training_label_v1",
    entities=[feature_window],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="label", dtype=Int64),
        Field(name="anomaly_type", dtype=String),
        Field(name="contributing_conditions_json", dtype=String),
        Field(name="incident_id", dtype=String),
        Field(name="approval_status", dtype=String),
        Field(name="rca_status", dtype=String),
    ],
    source=label_source,
    tags={"domain": "ims", "contract": "training-labels", "version": "v1"},
)
