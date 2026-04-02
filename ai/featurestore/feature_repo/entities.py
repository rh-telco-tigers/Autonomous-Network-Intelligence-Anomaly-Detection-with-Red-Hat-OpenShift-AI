from feast import Entity
from feast.value_type import ValueType


feature_window = Entity(
    name="feature_window",
    join_keys=["window_id"],
    value_type=ValueType.STRING,
    description="One persisted IMS feature window used for training and future serving retrieval.",
)
