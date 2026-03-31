# S-CSCF Overload Triage

S-CSCF overload typically presents as increased `5xx` responses, delayed `INVITE` handling, and growing dialog setup latency. Check upstream P-CSCF pressure, validate whether malformed traffic is bypassing early rejection, and review current thread or worker pool saturation before restarting the service.
