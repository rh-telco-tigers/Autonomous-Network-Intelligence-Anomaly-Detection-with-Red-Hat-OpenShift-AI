# P-CSCF Rate Limiting Guidance

If `REGISTER` traffic spikes and the P-CSCF shows elevated request concurrency, confirm whether the current load is expected test traffic or retry amplification. Apply temporary rate limiting only after confirming that the scenario is not a planned registration storm and that HSS latency is not the primary trigger.
