# Investigation architecture

The React workspace calls FastAPI using session cookies and CSRF tokens. Investigators initiate traces, taint computation, wallet-fence decisions and monitoring. Supervisors review freeze requests with two-factor elevation. PostgreSQL stores cases, durable jobs, graph evidence, time-ordered coloured ledgers and immutable audit records. Redis and Celery execute background work; local development uses the same durable job executor with an asyncio scheduler.

Adapters normalize Tron, Ethereum, BNB Smart Chain, Polygon and Bitcoin transfers. Explorer failures are surfaced with cache provenance; unsupported chains return explicit errors. Scenario-scoped adapters replay deterministic recorded transfers without mixing synthetic and live labels.

Monitoring scans approved fence members and addresses reached by coloured flows. Inclusive timestamp cursors retain all transaction identities at the boundary, preventing duplicate alerts without dropping simultaneous transfers. Outside crossings produce officer-decision proposals. Victims and inbound addresses tagged as victims cannot join the fence. A VASP crossing generates a disclosure pre-draft attached to the alert; impact, routing and officer review remain necessary before producing an actionable request.

Dormancy detection uses the interval since the preceding observed activity and the case's coloured balance immediately before the wake transfer, grouped by asset identity. Outgoing transfers qualify after the configured number of days when the historical log-amount z-score exceeds two or the amount reaches ten percent of the pre-wake coloured balance. Qualifying alerts queue a trace rooted at the awakened address followed by taint recomputation. Monitoring retries pending retraces on subsequent polls when another trace is active.

The migration adds fence_members, fence_proposals, monitor_settings, dormancy_state, alerts and benchmark_runs. All monitoring records belong to a case. Member and proposal identities and alert event keys have database uniqueness constraints. The data-model.json file describes all tables and keys; openapi.json is generated directly from the application.

## Prototype and production

| Component | Prototype | Production requirement |
| --- | --- | --- |
| Chain observations | Recorded scenarios and optional explorer APIs | Licensed indexed history, completeness guarantees and sustained rate-limit handling |
| Queue | Local durable executor or Redis/Celery | Monitored broker, dead-letter handling and multi-worker load evaluation |
| Attribution | Evidence-scored labels and probe fixtures | Independent current ground truth and held-out multi-provider evaluation |
| Public benchmark | 39 historical Binance chain-address labels | Independent transaction observations; no claim of live behavioural accuracy from label lookup |
| Fence monitoring | Configurable periodic incremental polling | Indexer subscriptions, address-history completeness and cross-worker locking |
| Requests | Explicit review and mock/optional integration | Approved FIU/VASP transport and deployment credentials |
| Video/deck | Demo script below | Recorded narrated video and submission slide deck still required |

## Measured local queue result

A run on this host consumed 1,000 independent case trace jobs with zero failures in 13.61 seconds: 73.46 jobs/second, 15.11 ms p95 trace execution and 13.61 ms average. This is a sequential local consumer using the same one-hop synthetic fixture across cases, not a concurrent production Redis/Celery throughput claim. Intake/setup time is excluded from the consumption measurement. Full measurements and held-out calibration bins are in benchmark-results.json.
