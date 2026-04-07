# RCA Knowledge Base

This directory contains seed RCA knowledge and a bootstrap script that loads it into Milvus for hybrid retrieval. The live ranker combines vector similarity, lexical overlap, and anomaly-aware KB metadata.

## Files

- `runbooks/*.json`: operational procedures, RCA guidance, and remediation knowledge bundles. JSON is the canonical runbook format.
- Canonical runbook article fields: `title`, `summary`, `category`, `anomaly_types`, and structured RCA guidance such as `recommended_rca`, `symptom_profile`, `evidence_to_collect`, and `operator_actions`
- `runbooks/anomaly-rca-knowledge.json`: anomaly-specific RCA anchor articles used to ground retrieval for every modeled anomaly type
- `incidents/*.json`: historical incidents with symptoms, root cause, and resolution
- `topology/*.json`: service relationships and dependency context
- `signal_patterns/*.md`: extracted SIP or log pattern guidance
- `bootstrap_knowledge.py`: loads the corpus into Milvus collections for `runbooks`, `incidents`, `topology`, and `signal_patterns`
