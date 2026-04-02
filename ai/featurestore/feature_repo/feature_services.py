from feast import FeatureService

from feature_views import ims_window_context_v1, ims_window_numeric_v1


ims_anomaly_scoring_v1 = FeatureService(
    name="ims_anomaly_scoring_v1",
    features=[
        ims_window_numeric_v1[
            [
                "register_rate",
                "invite_rate",
                "bye_rate",
                "error_4xx_ratio",
                "error_5xx_ratio",
                "latency_p95",
                "retransmission_count",
                "inter_arrival_mean",
                "payload_variance",
            ]
        ],
    ],
)

ims_anomaly_training_v1 = FeatureService(
    name="ims_anomaly_training_v1",
    features=[
        ims_window_numeric_v1[
            [
                "register_rate",
                "invite_rate",
                "bye_rate",
                "error_4xx_ratio",
                "error_5xx_ratio",
                "latency_p95",
                "retransmission_count",
                "inter_arrival_mean",
                "payload_variance",
            ]
        ],
        ims_window_context_v1[
            [
                "dataset_version",
                "source_snapshot_id",
                "scenario_name",
                "source",
                "feature_source",
                "transport",
                "call_limit",
                "rate",
                "approval_status",
                "rca_status",
            ]
        ],
    ],
)
