# SAHYOG VASP attribution engine

Tasks 1–13 of `VASP_Attribution_Engine_Implementation_Blueprint.md` are implemented as a runnable investigation workflow. New source code contains no comments.

| Task | Implemented |
|---|---|
| 1 | Cookie authentication, RBAC, TOTP, complaint intake, mock SAHYOG, screenshot/QR review |
| 2 | Tron, Ethereum, BNB, Polygon and Bitcoin adapters; hash resolution; profiles and balances; exact base-unit amounts; idempotent ingestion; database/file/Redis cache; replay; shared Redis token buckets and retry |
| 3 | Deterministic Easy, Hard and Multi-victim presets; truth export; synthetic adapter; CLI; noise, split/merge, bridge, swap, peel, farm, mixer and dormancy options |
| 4 | Durable asynchronous trace jobs; best-first traversal; bounded fan-out, hops, nodes and time; graph persistence; WebSocket events; stop reasons; bridge/swap transitions; peel, layering and chain-hop findings |
| 5 | Public label seeds and CSV import; sweeps, hubs, shared funder/contract fingerprints, ownership clustering and label propagation; evidence, recency, capped noisy-OR confidence, mixer scoring and synthetic isotonic calibration |
| 6 | Seeded simulated exchange probes; sweep aggregation; hub matching; freshness/staleness; simulator and CSV import; interface for real probing |
| 7 | Five mock VASPs; asynchronous hash and PSI lookup; verified labels; conflicting-claim review; response statistics/EWMA; routing recommendations and stablecoin issuer path |
| 8 | Exact Haircut/FIFO ledgers, Poison exposure bounds, dilution and mixer uncertainty, bridge/swap propagation, gang merging, restitution previews, officer-approved farms, operator similarities, private unit collisions and Louvain communities |
| 9 | Deposit/account impact estimates, depositor share and entropy/regularity features, configurable impact rules, swept/hub hard blocks and an exact-amount freeze slider |
| 10 | Two-key request state machine, session-bound TOTP, golden-hour deadlines and confirmation, mock VASP/issuer dispatch, amount reservations, retries, expiry/release/reminders, notifications and hash-chained audit verification |
| 11 | Immutable evidence snapshots, versioned PDF/JSON reports, public SHA-256 verification, individual label inclusion proofs, a Section 63 BSA preparation template and asset-separated analytics |
| 12 | Case stepper, shared graph and node evidence, chronological replay, case-colour filters, WebSocket updates, role-aware actions, English/Hindi shell and workspace aggregation |
| 13 | Simulated-path Markov training with a held-out evaluation, seeded Monte Carlo rollouts, next-hop/exit probabilities, conditional ETA ranges, forecast overlays, draft-only pre-staging and recency-weighted gang exit preferences |

The React workspace provides chain lookup, scenarios, case graphs and node inspection, attribution/evidence, federated status, routing, probe maps, public labels and VASP response histories. All synthetic data is marked. Monetary amounts remain decimal integer strings, including amounts above JavaScript's safe integer range.

## Docker startup

```sh
cp .env.example .env
docker compose up --build
```

Set separate random `JWT_SECRET` and `SAHYOG_SERVICE_TOKEN` values and a demo password in `.env` first. Compose starts Postgres 15, Redis 7, the backend, a Celery worker and beat scheduler, the frontend and both mock services. Migrations and idempotent seeding run before the backend starts.

Open http://localhost:5173. API documentation: http://localhost:8000/docs. Mock portal: http://localhost:8001/docs. Mock VASPs: http://localhost:8002/docs.

Accounts are `investigator@sahyog.demo`, `supervisor@sahyog.demo` and `admin@sahyog.demo`, with the configured `DEMO_PASSWORD`. Seeding preserves existing accounts/passwords. Investigators create cases and run investigations; admins manage labels, replay, probes and calibration; all roles can inspect results.

```sh
docker compose exec backend python -m app.demo_totp --email supervisor@sahyog.demo
```

Use `--provision` for the demo authenticator URI. Enrollment for other accounts is available in the UI.

## Guaranteed demo

1. Log in as investigator and open **Scenarios**.
2. Generate **Hard**, then **Load as case**.
3. Run the trace. Inspect deposit/hub nodes and transfer amounts.
4. Run attribution. The seeded probe match produces 90% inferred confidence.
5. Ask VASPs using salted hash or PSI. A positive mock reply confirms ownership at 95%.
6. Recommend the channel to see the portal/issuer route and responsiveness ranking.

Easy includes scenario-scoped labels. Multi-victim creates five separate complaints sharing one laundering path. Scenario truth is available through the UI/API. Run Dye Pack on Multi-victim to see 14,000 traceable and 9,000 uncoloured at arrival, plus five separate victim allocations. The preset has already swept to a hub, so Impact blocks a new on-chain freeze and permits a disclosure draft. Wallet-farm analysis is available now; fence and sleeper monitoring remain Tasks 14–16.

## Local development

Use Python 3.11 for pinned dependencies, plus Tesseract and libzbar for OCR/QR. Docker includes both native tools. Configure `JWT_SECRET`, `SAHYOG_SERVICE_TOKEN`, `SEED_DEMO=true` and `DEMO_PASSWORD` in `backend/.env` or export them. Local defaults use SQLite and in-process asynchronous jobs.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd backend
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m app.seed
../.venv/bin/python -m uvicorn app.main:app --reload
```

Run the mock services in separate terminals from `backend`:

```sh
PYTHONPATH=..:. ../.venv/bin/python -m uvicorn mock_services.sahyog.main:app --port 8001
PYTHONPATH=..:. ../.venv/bin/python -m uvicorn mock_services.vasps.main:app --port 8002
```

```sh
cd frontend
npm ci
npm run dev
```

For local Celery, set `JOB_BACKEND=celery` and `REDIS_URL` on both backend and worker, then run:

```sh
cd backend
../.venv/bin/python -m celery -A app.workers.tasks:celery worker --loglevel=info --concurrency=2
```

```sh
cd backend
../.venv/bin/python -m app.dev.gen --preset hard --seed 42 --load
```

## Tasks 8–13 walkthrough

1. Load a Multi-victim case, trace it, then select **Taint** and run Dye Pack. Compare FIFO and Haircut ranges, inspect the colour legend and open the gang restitution/community view.
2. Select **Impact**. A swept or hub target is blocked; use **Approval** to prepare a disclosure request. Unswept eligible deposits support exact traceable amounts, capped by the current impact and existing requests.
3. Propose with a written reason. In a separate supervisor session, open the request timeline, enter a current authenticator code and approve. The timeline records dispatch and mock responses; the bell shows updates.
4. Generate **Report**, download the PDF/JSON and open **Verify seal**. Upload the JSON to verify it; changing a field fails verification. Select an evidence item to export its inclusion proof.
5. Generate at least six scenarios. As admin, open a case's **Forecast** step and train the model. Return as investigator to run 1,000 rollouts, inspect the simulated evaluation/ETA and pre-stage an unsent draft. Three distinct prior gang exits enable the preference blend.
6. Use the time scrubber and case-colour filter to inspect the observed path. The shell language selector switches key navigation/step labels to Hindi.

The local backend runs a five-second expiry/status scheduler. For Celery deployments, the worker receives ETA deadlines and beat reconciles status/expiry every thirty seconds; run `celery -A app.workers.tasks:celery beat --loglevel=info` alongside the worker. Report files use the persistent `reports` volume. Impact thresholds and evidence freshness are configured in `backend/app/config/impact.json`.

## Live adapters and replay

Replay is enabled by default and never calls chain providers. Synthetic scenarios use their stored adapter directly. A live request without recorded data returns `REPLAY_CACHE_MISS`. Disable replay as admin under **Chain lookup** or through `POST /api/v1/admin/replay-mode` to record provider responses. Configure `ETHERSCAN_API_KEY` and optional `TRON_API_KEY`. Re-enable replay to reproduce recorded investigations during an outage. Histories have explicit bounded pagination and return a limit error instead of silently truncating.

Supported live protocol decoding is CCTP v1 between Ethereum and Polygon and Ethereum Uniswap V2 router receipts. CCTP requires a matching nonce/source message and independently observed destination USDC mint; missing confirmation stops for manual follow-up. The destination search is bounded to 24 hours. Unsupported bridges stop explicitly.

For a swap service without receipt events, an admin can configure `PUT /api/v1/admin/swap-services` with a list of `SwapServiceInput` records from OpenAPI: input/destination chain, deposit/output wallets, exact token contracts, externally supplied rate/fee, bounded window/tolerance and an evidence reference. Matching only accepts a clearly separated candidate. Inferred links appear dashed and retain the matching evidence; the system does not invent exchange rates.

Bitcoin outputs retain the joint input set and a representative sender; this does not assert a per-input flow allocation. Common-input/change clustering is probabilistic and suppressed for suspected equal-denomination CoinJoin transactions. Public labels for Binance and Kraken retain historical source links and dates; they are not verified VASP responses. Synthetic labels and confirmations cannot leak into unrelated scenarios or live investigations.

Calibration fits attributed synthetic cases through the admin **Scenarios** control or `POST /api/v1/dev/calibration/train`. It applies only to synthetic cases and does not establish measured production accuracy. Conflicting VASP claims force `needs_review`, zero confidence and block automatic re-attribution of that trace.

## Privacy and prototype boundaries

Salted HMAC queries omit raw addresses but remain susceptible to address guessing by a recipient that knows the salt. The optional PSI protocol compares blinded sets with fresh scalars and expiring sessions. It is a prototype implementation, not an independently audited production protocol.

Probe seeds/runs, mock VASPs and mock SAHYOG are simulated. Real probing is an interface, and real SAHYOG returns `INTEGRATION_NOT_CONFIGURED`. Routing prepares a recommendation. An investigator can then propose an observed evidence package and a different supervisor can approve it with session-bound TOTP. Authorized dispatch uses the mock client; forecasts cannot authorize or send requests. Temporary holds lapse without a timely second key, and expiry/release is delivered to both portal and issuer holds. Real SAHYOG still fails closed.

Audit entries, request timelines and report snapshots are append-only through the application. Hash-chain and report verification detect direct database tampering; deployment access controls must protect the database and report volume. Public verification returns seal metadata without case contents. Cross-unit collision notices conceal the other case; coordination rooms restrict access to the participating units.

Taint is a methodological convention. Uncertain amounts overlap uncoloured balances and are never added as extra money. Poison exposure bounds can overlap between victims and are excluded from restitution and freeze sizing. Reports preserve the evidence date and explicitly identify simulated material. The certificate is a preparation template requiring legal review against the statutory Schedule: [Bharatiya Sakshya Adhiniyam, 2023](https://www.indiacode.nic.in/indiacode/bitstream/123456789/20063/1/aa202347.pdf).

Protocol/provider references: [Tron API](https://developers.tron.network/docs/api), [Etherscan API](https://docs.etherscan.io/), [Esplora API](https://github.com/Blockstream/esplora/blob/master/API.md), [Circle TokenMessenger](https://github.com/circlefin/evm-cctp-contracts/blob/master/src/TokenMessenger.sol), [Circle MessageTransmitter](https://github.com/circlefin/evm-cctp-contracts/blob/master/src/MessageTransmitter.sol). Public-address evidence links are stored with each seeded label.

## Verification

```sh
cd backend
../.venv/bin/python -m pytest -q
../.venv/bin/ruff check app tests migrations ../mock_services
cd ../frontend
npm run build
CHROMIUM_PATH=/usr/bin/chromium npm run test:e2e
```

Playwright starts isolated backend/mock/Vite services and test databases. Alternatively install its browser with `npx playwright install chromium`. Coverage includes complaint intake, image review, auth/RBAC, migrations, adapter fixtures, replay without network, deterministic truth, traces/patterns, attribution/probe freshness, both privacy modes, conflicting ownership claims and routing, exact multi-victim allocations, random ledger conservation, impact rules, two-key separation, hold expiry/extension/release, concurrent audit writes, immutable seals and tamper rejection, inclusion proofs, gang priors, forecast reproducibility, public verification and Hindi navigation.

Validation passed: 73 backend tests, 6 Chromium end-to-end tests, the TypeScript/Vite production build, Ruff and migration upgrade/schema comparison/downgrade. Separate local Redis/Celery integration checks completed a queued trace, queued PSI confirmation, shared provider rate limiting, queued request dispatch, ETA expiry and external release. Compose YAML was also validated.

Validation on this host used Python 3.14 and Chromium. Docker, Postgres and Python 3.11 were unavailable, so the complete pinned Compose stack was not executed. Live providers require configured credentials and have been tested using recorded contract fixtures rather than production API calls. QR decoding was exercised; native Tesseract's unavailable-engine fallback was tested because Tesseract is absent on this host.


## Tasks 14–16: monitoring and hardening

Run migrations before restarting the application. Fence and Dormancy are available in the case workspace. Investigators approve members and proposals; victims cannot be added. Polling is configurable per case. Synthetic replay advances the observation clock explicitly. A qualifying dormant wake queues tracing from that address and refreshes taint. VASP alerts contain reviewable disclosure pre-drafts and never dispatch requests automatically.

One-command container startup: `bash scripts/demo.sh`.

Benchmark: with the normal backend environment variables loaded, run `.venv/bin/python scripts/benchmark.py --cases 1000`. It creates independent synthetic cases and consumes their durable trace jobs, reports throughput/p95/failures, and persists a report visible at `/benchmarks`. Run it against an isolated benchmark database to avoid populating operational cases. Results distinguish held-out synthetic calibration from the historical public-address corpus. The 39 chain-address labels come from [Binance's published November 2022 wallet snapshot](https://www.binance.com/en-IN/blog/community/2895840147147652626); they are not evidence of current ownership or live behavioural attribution accuracy.

See `docs/submission/architecture.md`, generated OpenAPI and data model, benchmark results and six-minute demo runbook. The independent live behavioural public-address evaluation, narrated video and submission slide deck remain outstanding. The supplied blueprint ends at task 16; task 17 has not been specified.
