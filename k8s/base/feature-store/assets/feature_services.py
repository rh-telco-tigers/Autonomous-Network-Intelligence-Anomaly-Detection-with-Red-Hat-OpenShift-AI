from feast import FeatureService

from feature_views import ani_window_context_v1, ani_window_numeric_v1


ani_anomaly_scoring_v1 = FeatureService(
    name="ani_anomaly_scoring_v1",
    features=[
        ani_window_numeric_v1[
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

ani_anomaly_training_v1 = FeatureService(
    name="ani_anomaly_training_v1",
    features=[
        ani_window_numeric_v1[
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
        ani_window_context_v1[
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
