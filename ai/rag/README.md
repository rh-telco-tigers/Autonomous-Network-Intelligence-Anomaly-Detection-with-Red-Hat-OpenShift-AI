# RCA Knowledge Base

This directory contains seed RCA knowledge and a bootstrap script that loads it into Milvus using a deterministic hash embedding.

## Files

- `runbooks/*.md`: operational procedures and remediation guidance
- `incidents/*.json`: historical incidents with symptoms, root cause, and resolution
- `topology/*.json`: service relationships and dependency context
- `signal_patterns/*.md`: extracted SIP or log pattern guidance
- `bootstrap_knowledge.py`: loads the corpus into Milvus collections for `runbooks`, `incidents`, `topology`, and `signal_patterns`
