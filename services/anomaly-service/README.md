# anomaly-service

Scores feature windows with the deployed predictive model and decides whether they represent anomalous IMS behavior.

When a score crosses the threshold, it creates an incident in the control plane so RCA and operator actions can continue from a single record.
