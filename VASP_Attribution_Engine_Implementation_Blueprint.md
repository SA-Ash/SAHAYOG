# Automated Blockchain Intelligence & VASP Attribution Engine
## Implementation Blueprint: Sequential Tasks

Each task has: **Functional Requirements, Libraries, Routes, Controllers/Services, Data Tables, UI Components, Decisions, Implementation Steps, Takeaways for the next task.**
Build the tasks in order. Every task ends with a working, testable slice, and its takeaways list what the next task consumes.

---

## 0. Global Conventions

### 0.1 Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn | Fast to build, typed, auto-generated OpenAPI docs (good for the SAHYOG API contract) |
| ORM / migrations | SQLAlchemy 2.x, Alembic | Standard, migration history |
| DB | PostgreSQL 15 | Cases, labels, audit, edges |
| Graph | NetworkX (in memory per trace) + edges stored in Postgres. Neo4j is optional/stretch | Removes a moving part; enough for demo scale |
| Queue / cache | Redis, Celery (or RQ) | Trace jobs, API response cache, rate limiting |
| HTTP client | httpx, tenacity | Async calls with retry/backoff |
| Auth | PyJWT, passlib[bcrypt], pyotp | JWT sessions, password hashing, TOTP 2FA for approvals |
| Chain decoding | base58, eth-abi, eth-utils | Tron address conversion, EVM log decoding for bridges |
| Math | `decimal.Decimal`, numpy, scipy | Exact amounts, Markov model, z-scores |
| Algorithms extra | networkx (Louvain, components), scikit-learn (isotonic, logistic), pytesseract + opencv (OCR), cryptography/pynacl (PSI), merkletools | Add-on algorithms |
| Reports | reportlab (or WeasyPrint), qrcode, hashlib | PDF + QR + SHA-256 |
| Testing | pytest, hypothesis, pytest-asyncio | Property tests for value conservation |
| Frontend | React 18, Vite, TypeScript | |
| UI libs | Tailwind CSS, shadcn/ui, lucide-react | |
| Data fetching | TanStack Query, axios | Caching, polling |
| State | Zustand | Light global state (selected case, graph selection) |
| Graph UI | Cytoscape.js + cytoscape-dagre (or react-flow as alternative) | Compound nodes, styling, layouts |
| Charts | Recharts | Pie (Dye Pack), timeline (Sleeper), bars |
| Forms | react-hook-form, zod | Validation |
| Realtime | Native WebSocket (FastAPI) | Trace progress, approval notifications |
| DevOps | Docker Compose | `docker compose up` for the whole demo |

### 0.2 Repository layout

```
/backend
  /app
    /api            # routers (one file per module)
    /services       # controllers / business logic
    /adapters       # chain adapters
    /engines        # trace, attribution, taint, forecast
    /models         # SQLAlchemy models
    /schemas        # Pydantic schemas
    /core           # config, security, logging, deps
    /workers        # celery tasks
  /tests
/mock_services
  /sahyog           # mock portal (separate FastAPI app)
  /vasps            # 4-5 mock exchanges (one app, many routes/ports)
/frontend
  /src/pages /src/components /src/api /src/store
/data               # seeds, synthetic scenarios, cached API responses
docker-compose.yml
```

### 0.3 Cross-cutting decisions (apply to every task)

| Decision | Rule |
|---|---|
| Amount handling | Store as `NUMERIC(38,0)` in base units + `decimals` per token. Never use floats for money |
| Address normalization | Lowercase for EVM, base58 for Tron, bech32/base58 for BTC. Always store `(chain, address)` as the key |
| API prefix | `/api/v1` |
| IDs | UUID for entities, human-readable `case_ref` for cases |
| Errors | Single JSON error shape `{code, message, details}` |
| Auditing | Every state-changing endpoint and every sensitive read writes an `audit_log` entry (added in Task 10; stub the hook from Task 1) |
| Explainability | Every computed value returns an `evidence` array saying why |
| Simulated parts | Any simulated component sets `source = "demo_seed"` or `"simulated"` and the UI shows a badge |

---

## TASK 1: Foundation, Auth/RBAC and Case Intake (mock SAHYOG)

### Functional Requirements
- FR-1.1 Users log in with role: `INVESTIGATOR`, `SUPERVISOR`, `ADMIN`.
- FR-1.2 Supervisor approvals require TOTP 2FA (enforced later, set up here).
- FR-1.3 Create a case from a SAHYOG complaint: victim wallet, suspect wallet A, chain, tx hash, amount, reference number.
- FR-1.4 Accept only a tx hash as input (derive suspect address from it in Task 2).
- FR-1.5 List, filter and open cases. Link multiple victims to one gang-case later.
- FR-1.6 Mock SAHYOG app exposes the same API contract the real integration would use.

### Libraries
FastAPI, SQLAlchemy, Alembic, PyJWT, passlib, pyotp, pydantic-settings; React Router, TanStack Query, shadcn/ui.

### Routes

| Method | Route | Purpose | Role |
|---|---|---|---|
| POST | `/auth/login` | Email + password, returns JWT | any |
| POST | `/auth/2fa/verify` | Verify TOTP, returns elevated token | any |
| GET | `/auth/me` | Current user | any |
| POST | `/cases` | Create case | Investigator |
| GET | `/cases` | List with filters (status, chain, date) | any |
| GET | `/cases/{id}` | Case detail | any |
| POST | `/cases/{id}/victim-transactions` | Add another victim transaction | Investigator |
| POST | `/integrations/sahyog/webhook` | Receive new complaint from SAHYOG | service token |

Mock SAHYOG app: `POST /sahyog/complaints`, `GET /sahyog/complaints/{ref}`, `POST /sahyog/requests`, `GET /sahyog/requests/{id}`.

### Controllers / Services
- `AuthService`: `login()`, `issue_token()`, `verify_totp()`
- `CaseService`: `create_case()`, `attach_victim_tx()`, `list_cases()`
- `SahyogClient`: interface with `MockSahyogClient` and a stub `RealSahyogClient`. Swap through config.
- `deps.require_role(*roles)`: FastAPI dependency.

### Data Tables

| Table | Columns |
|---|---|
| `users` | id, name, email, password_hash, role, totp_secret, org_unit, is_active |
| `cases` | id, case_ref, sahyog_ref, status (`OPEN, TRACING, ATTRIBUTED, FREEZE_PENDING, CLOSED`), gang_case_id (nullable), created_by, created_at |
| `victim_transactions` | id, case_id, chain, tx_hash, victim_address, suspect_address, token, amount, tx_time, status |

### UI Components
`LoginPage`, `TwoFactorPrompt`, `AppShell` (sidebar, role badge), `CaseListPage` (table, filters), `NewCaseDialog` (form with zod validation), `CaseDetailHeader`, `SimulatedBadge`.

### Decisions
- The integration sits behind a `SahyogClient` interface, so replacing the mock with the real API changes one class.
- JWT in an httpOnly cookie, short expiry, and a separate elevated token for approvals.
- Seed 3 users (investigator, supervisor, admin) for the demo.

### Implementation Steps
1. Docker Compose with Postgres, Redis, backend, frontend, mock SAHYOG.
2. Alembic migration for `users`, `cases`, `victim_transactions`.
3. Auth endpoints and the `require_role` dependency.
4. Case CRUD plus the webhook receiver.
5. Frontend login, case list, new-case form.
6. Add `audit(actor, action, payload)` as a no-op stub that Task 10 will implement.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Address checksum validation | Core | Base58Check: decode, then last 4 bytes must equal first 4 bytes of SHA256(SHA256(payload)). EIP-55: hash the lowercase hex with Keccak-256 and uppercase each letter whose hash nibble is ≥ 8. Bech32: polymod checksum |
| Role-based access check | Core | Dependency-injected role guard on every route |
| **OCR + regex extraction** | **Add-on** | Pipeline: grayscale → adaptive threshold → Tesseract → regex candidates → checksum filter → confidence rank |

**OCR regexes:** EVM `0x[a-fA-F0-9]{40}`, Tron `T[1-9A-HJ-NP-Za-km-z]{33}`, Bitcoin (legacy/bech32 patterns), tx hash `(0x)?[a-fA-F0-9]{64}`. The checksum filter removes OCR misreads (`O`/`0`, `l`/`1`).

**Added requirements (add-on)**
- FR-1.7 Upload a victim screenshot or QR image and extract candidate addresses, hashes, and amounts.
- FR-1.8 Officer reviews and confirms extracted fields before the case is created (never auto-submit OCR output).

**Added libraries:** pytesseract, opencv-python-headless, Pillow, pyzbar (QR decoding).
**Added route:** `POST /cases/extract-from-image` → `{candidates: [{type, value, checksum_ok, confidence}]}`
**Added table:** `case_attachments` (id, case_id, file_path, sha256, extracted_json, uploaded_by)
**Added UI:** `ScreenshotDropzone`, `ExtractedFieldsReview` (editable chips with checksum tick/cross)

### Takeaways for Task 2
- A case exists with a victim tx (hash or suspect address) and a status field.
- `case_id` is the foreign key that every later module uses.
- The audit stub is ready to be filled.

---

## TASK 2: Chain Adapters and Ingestion

### Functional Requirements
- FR-2.1 One interface for all chains: Tron, Ethereum, BNB Chain, Polygon, Bitcoin (Solana as stretch).
- FR-2.2 Resolve a tx hash to sender, receiver, token, amount, time.
- FR-2.3 Fetch outgoing and incoming transfers for an address within a time range.
- FR-2.4 Fetch auxiliary facts: account activation source (Tron), gas-funder (EVM), contract-type flag.
- FR-2.5 Normalize every response to one `Transfer` schema.
- FR-2.6 Cache every response; support a replay mode that never hits the network.
- FR-2.7 Return `UNSUPPORTED_CHAIN` cleanly for anything not implemented.

### Libraries
httpx, tenacity, redis-py, base58, eth-utils, pydantic.

### Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/chains` | Supported chains and adapter health |
| GET | `/chain/{chain}/tx/{hash}` | Normalized tx |
| GET | `/chain/{chain}/address/{addr}/transfers?direction=out&from=&to=` | Transfers |
| GET | `/chain/{chain}/address/{addr}/profile` | First-seen, last-seen, tx count, activation/gas funder, is_contract |
| POST | `/admin/replay-mode` | Toggle replay mode |

### Controllers / Services
- `ChainAdapter` (abstract): `get_tx()`, `get_transfers()`, `get_profile()`, `get_balance()`
- `TronAdapter` (TronGrid), `EvmAdapter` (Etherscan-family, one class parameterized by chain), `BitcoinAdapter` (mempool.space/Blockstream)
- `AdapterRegistry.get(chain)`
- `HttpCache` (Redis + file-backed JSON in `/data/cache`), `RateLimiter` (token bucket per provider)

### Data Tables

| Table | Columns |
|---|---|
| `transfers` | id, chain, tx_hash, log_index, from_addr, to_addr, token, amount, decimals, block_time, source (`live/cache/synthetic`) |
| `address_profiles` | chain, address, first_seen, last_seen, tx_count, activated_by, gas_funder, is_contract, fetched_at |
| `api_cache` | key, response_json, fetched_at, provider |

Unique key on `transfers (chain, tx_hash, log_index)` makes ingestion idempotent.

### UI Components
`ChainStatusPanel` (adapter health, replay toggle), `TxLookupCard`, `AddressProfileCard`.

### Decisions
- One `Transfer` schema is the contract for all chains. Everything downstream is chain-agnostic.
- Prioritize **Tron USDT + Ethereum/BNB USDT** deeply, Bitcoin shallowly, and mark the rest "planned".
- Replay mode is a first-class feature because it makes the judged demo immune to API failure.

### Implementation Steps
1. Define `Transfer` and `AddressProfile` pydantic models.
2. Build Tron adapter first (TRC20 transfers plus account activation lookup).
3. Build the EVM adapter (token transfers plus internal transactions).
4. Add Bitcoin (inputs/outputs mapped to transfers).
5. Add caching and replay.
6. Contract tests: run the same tests against each adapter on recorded fixtures.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Tron hex ↔ base58 conversion | Core | `0x41` prefix + 20-byte hash → Base58Check |
| Token-bucket rate limiter | Core | Per-provider limits, refill at the provider's req/s |
| Exponential backoff with jitter | Core | `delay = min(cap, base·2^n) · random(0.5, 1.0)` via tenacity |
| Deterministic cache key | Core | `sha256(provider + path + sorted(params))` |
| Idempotent upsert | Core | `INSERT … ON CONFLICT (chain, tx_hash, log_index) DO NOTHING` |

### Takeaways for Task 3
- Anything on-chain can be fetched in one normalized shape, live or from cache.
- Because replay exists, a synthetic scenario can be injected through the same interface as real data.

---

## TASK 3: Synthetic Scenario Generator and Test Ground Truth

### Functional Requirements
- FR-3.1 Generate a laundering scenario with known answers: N victims, collection wallets, merges, K hops, a sweep into a hub, an unrelated noise population, and optionally one bridge hop.
- FR-3.2 Emit ground truth: which address is the deposit address, which is the hub, which exchange, exact per-case amounts.
- FR-3.3 Serve scenarios through a `SyntheticAdapter` so the rest of the system cannot tell the difference.
- FR-3.4 Support parameters: noise level, split/merge intensity, mixer insertion, dormant wallet.

### Libraries
numpy, faker, pydantic, pytest.

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/dev/scenarios` | Generate a scenario with params |
| GET | `/dev/scenarios/{id}/truth` | Ground truth JSON |
| POST | `/dev/scenarios/{id}/load-as-case` | Create cases from it |

### Controllers / Services
`ScenarioGenerator` (seeded RNG for reproducibility), `SyntheticAdapter` (implements `ChainAdapter` over stored scenario data), `ScenarioLoader`.

### Data Tables

| Table | Columns |
|---|---|
| `scenarios` | id, name, params_json, seed, created_at |
| `scenario_truth` | scenario_id, deposit_address, hub_address, entity, per_case_amounts_json, untraceable_amount |

Scenario transfers go into `transfers` with `source = 'synthetic'`.

### UI Components
`ScenarioPanel` (dev-only page): preset buttons ("5 victims, 4 hops", "with bridge", "with mixer"), seed input, load button.

### Decisions
- Fixed seeds produce identical demo runs.
- Ship **three presets**: Easy (labels exist), Hard (no labels, sweep + hub), Multi-victim (5 cases, Dye Pack).
- Ground truth makes accuracy and calibration numbers possible later.

### Implementation Steps
1. Write the generator: victims → collection wallets → merge → hops → deposit address → hub → withdrawals; add noise wallets and transfers.
2. Implement `SyntheticAdapter`.
3. Write the truth export.
4. Add the three presets and a CLI (`python -m app.dev.gen --preset hard`).

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Seeded RNG | Core | Same seed → same scenario |
| Poisson arrival process | Core | Timing of noise transactions |
| Log-normal delay sampling | Core | Sweep delays and hop delays |
| **Peel-chain injector** | **Add-on** | Creates ground truth for the peel-chain detector (Task 4) |
| **Wallet-farm injector** | **Add-on** | Many fresh wallets funded by one parent (ground truth for Task 8) |
| **Mixer injector** | **Add-on** | Equal-denomination deposits and fresh-address withdrawals |
| **Dormancy injector** | **Add-on** | A tainted wallet idle for N days, then active (Task 15) |

**Added scenario parameters:** `peel_chain_len`, `farm_size`, `mixer_denoms`, `dormant_days`, `operator_profile` (activity hours, fee style) for operator fingerprinting tests.

### Takeaways for Task 4
- There is a known-answer dataset to validate the trace engine.
- Trace code only needs `AdapterRegistry`, so it works unchanged on real data.

---

## TASK 4: Trace Engine

### Functional Requirements
- FR-4.1 Forward trace from the suspect address, hop by hop, until a stop condition.
- FR-4.2 Configurable limits: max hops, min amount, max fan-out per node, time window.
- FR-4.3 Prune dust and obvious high-volume noise.
- FR-4.4 Stop conditions: labeled entity reached, hub detected, mixer reached, hop limit, no outgoing funds.
- FR-4.5 Bridge/swap decoding: continue the trace on the destination chain (1-2 well-supported bridges).
- FR-4.6 Run asynchronously with progress updates over WebSocket.
- FR-4.7 Re-running a trace is idempotent.

### Libraries
Celery, networkx, eth-abi (event decoding), websockets (FastAPI).

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/trace` | Start trace job (params in body) |
| GET | `/cases/{id}/trace` | Latest trace result (nodes, edges, stop reasons) |
| GET | `/jobs/{job_id}` | Job status and progress |
| WS | `/ws/cases/{id}` | Live events: `node_added`, `edge_added`, `trace_done` |
| GET | `/cases/{id}/graph` | Graph in Cytoscape format |

### Controllers / Services
- `TraceService.start(case_id, params)`
- `TraceEngine.run()`: BFS with a priority queue ordered by amount carried, to chase the largest flows first
- `BridgeDecoder`: registry of known bridge contract addresses and event ABIs → destination chain/recipient
- `StopRules`: pluggable rule list (label, mixer, hub, limits)
- `trace_worker` Celery task

### Data Tables

| Table | Columns |
|---|---|
| `trace_runs` | id, case_id, params_json, status, started_at, finished_at, stop_summary_json |
| `graph_nodes` | id, trace_run_id, chain, address, hop, role (`unknown/deposit_candidate/hub_candidate/exchange/mixer/bridge/victim`), cluster_id, label_id |
| `graph_edges` | id, trace_run_id, from_node, to_node, transfer_id, amount, token |

### UI Components
`CaseGraph` (Cytoscape canvas, dagre layout), `NodeInspector` side panel (address, chain, role, evidence), `TraceControls` (hop/min-amount sliders, run button), `TraceProgressBar`, `StopReasonLegend`.

### Decisions
- Walk the largest flows first, and cap fan-out, so graphs stay readable.
- Persist the trace graph per run (reproducibility for evidence).
- Bridges: support one EVM bridge and one swap router properly, and flag the rest as "unsupported bridge, manual follow-up".
- Mixers are a stop condition and are reported as "probabilistic, uncertain".

### Implementation Steps
1. Implement BFS trace with limits against the synthetic adapter.
2. Add stop rules and node roles.
3. Add WebSocket progress events.
4. Persist nodes/edges.
5. Frontend graph rendering with live updates.
6. Add bridge decoding with fixtures.
7. Test: on the Hard preset, the trace reaches the deposit address and hub from the truth file.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Best-first search | Core | Priority queue keyed by amount carried, largest flows first |
| Pruning heuristics | Core | Dust threshold, fan-out cap, known-noise filter (very high-degree addresses) |
| Time-window filtering | Core | Only follow transfers dated after the incoming one |
| Bridge event decoding | Core | Parse ABI logs → destination chain and recipient |
| **Swap amount-and-time matching** | **Add-on** | For swap services with no event data |
| **Peel-chain detection** | **Add-on** | Flag classic laundering shape |
| **Layering / chain-hopping detection** | **Add-on** | Split-then-merge and repeated chain changes |

```
best_first_trace(start, limits):
    pq = [(−amount_in, start, hop=0)]
    while pq and not limit_reached:
        node = pop_largest(pq)
        if stop_rule(node): mark(node, reason); continue
        outs = adapter.get_transfers(node, out, after=node.arrival_time)
        outs = prune(outs, dust, fanout_cap)
        for t in outs:
            if is_bridge(t.to): t = decode_bridge(t)   # cross-chain jump
            push(pq, (−t.amount, t.to, hop+1))
            add_edge(node, t)
```

**Swap matching (add-on):** for a deposit of amount `a` into a swap router at time `t`, candidate outputs are transfers within `[t, t+Δ]` on the destination chain with `|out − a·rate·(1−fee)| / a < ε`. Score = amount error + time gap; accept the top candidate only if its margin over the second is large. Mark the edge `inferred` and keep the confidence.

**Peel-chain rule:** at each hop the node has two outputs: a small one (< 15% of input) going to an end address or known service, and a large remainder going to a fresh address that continues the chain. Flag after ≥ 4 consecutive hops.

**Layering / chain-hopping rule:** layering = a "diamond" subgraph (a split followed by a re-merge within a time window T). Chain-hopping = ≥ 2 chain changes on one path.

**Added requirements (add-on)**
- FR-4.8 Detect and label peel chains, layering, and chain-hopping on the trace graph.
- FR-4.9 Infer swap-service links and mark them probabilistic.

**Added route:** `GET /cases/{id}/patterns`
**Added table:** `pattern_findings` (id, trace_run_id, kind `PEEL/LAYERING/CHAIN_HOP/SWAP_INFERRED`, node_ids_json, score, evidence_json)
**Added UI:** `PatternPanel`, `PatternBadge` on graph nodes ("Peel chain, 6 hops"), `InferredEdgeStyle` (dashed edges)

### Takeaways for Task 5
- Output is a persisted graph with `deposit_candidate` and `hub_candidate` nodes still unlabeled.
- Nodes carry enough metadata (timings, counterparties) for sweep/hub analysis.
- The graph viewer already has a spot to show a label and confidence on a node.

---

## TASK 5: Label Store and Attribution Engine (core of the problem statement)

### Functional Requirements
- FR-5.1 Exact label lookup (address → entity) with source and date.
- FR-5.2 Sweep detection: an address that receives funds and forwards (almost) all of it within a short window, repeatedly, to the same target is a **deposit-address candidate**; the target is a **hub candidate**.
- FR-5.3 Hub validation: many unrelated counterparties, high volume, many sweeps in.
- FR-5.4 Infrastructure fingerprints: shared activation/gas funder (Tron/EVM), shared sweep contract.
- FR-5.5 Address clustering: group addresses by common funder, common sweep target, and (Bitcoin) common-input ownership; spread a label across the cluster.
- FR-5.6 Match hubs against the hot-wallet map (Task 6) and federated results (Task 7).
- FR-5.7 Produce an attribution result: entity, confidence, evidence list, hops from origin.
- FR-5.8 Classify nodes: exchange cluster, hot wallet, deposit wallet, mixer, bridge, swap service.
- FR-5.9 Verified labels (confirmed by a VASP reply) override inferred ones.

### Libraries
networkx (community/clustering), pandas (window analysis), scikit-learn (optional, calibration), numpy.

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/attribute` | Run attribution on the latest trace |
| GET | `/cases/{id}/attribution` | Result with entity, confidence, evidence |
| GET | `/labels?address=&chain=` | Lookup |
| POST | `/labels` | Add/import labels (admin) |
| GET | `/clusters/{id}` | Cluster members and evidence |
| GET | `/nodes/{id}/why` | Explanation panel data |

### Controllers / Services
- `LabelService`: lookup, import CSV, record verified label
- `SweepDetector`: sliding-window analysis, output `(address, sweep_target, count, median_delay)`
- `HubValidator`: counterparty count, volume, in/out ratio
- `FingerprintService`: funder linkage and shared-contract detection
- `ClusterService`: union-find over linkage evidence
- `ConfidenceScorer`: noisy-OR combination (below)
- `AttributionService`: orchestrates the above and writes the result

### Confidence model
```
confidence = 1 - Π (1 - w_i)   over signals that fired
```
Starting weights (tune on synthetic + public data):

| Signal | w |
|---|---|
| Exact public label | 0.70 |
| Sweep pattern (≥ N sweeps) | 0.50 |
| Hub validated | 0.30 |
| Shared funder/fingerprint | 0.40 |
| Probe-map match | 0.80 |
| Federated VASP confirmation | 0.95 |

Cap inferred-only attributions at 0.90; only verified labels may exceed it.

### Data Tables

| Table | Columns |
|---|---|
| `entities` | id, name, type (`exchange/custodial/mixer/bridge/swap/otc`), country, vasp_id (nullable) |
| `labels` | id, chain, address, entity_id, source (`public/probe/federated/inferred/verified`), evidence_json, confidence, created_at, verified_by |
| `clusters` | id, chain, entity_id (nullable), kind, created_from |
| `cluster_members` | cluster_id, address, evidence |
| `attributions` | id, case_id, trace_run_id, entity_id, confidence, hops, evidence_json, status (`inferred/confirmed`) |

### UI Components
`AttributionCard` (entity, confidence ring, hops), `EvidenceList` (each signal with weight), `NodeBadge` (role colour on the graph), `WhyDrawer` (explains the label), `CandidateTable` (when confidence is below threshold).

### Decisions
- Attribute the **hub**, not the individual deposit address.
- Labels carry `source` and `evidence`; the UI always shows both.
- Below a threshold (say 0.6) the output is a ranked candidate shortlist and the system auto-triggers the federated lookup (Task 7).
- Calibration is done once ground truth exists (Task 16).

### Implementation Steps
1. Label tables plus CSV importer with 2-3 exchanges of public data.
2. `SweepDetector`, tested against the synthetic truth.
3. `HubValidator` and clustering.
4. Scorer and attribution orchestration.
5. UI: node colouring, attribution card, why-drawer.
6. Test: on the Hard preset, attribution returns the right entity once the probe map (Task 6) is seeded.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| **Sweep detection (sliding window)** | Core | See pseudocode below |
| Hub validation | Core | Unique counterparties, volume, in/out ratio, number of distinct deposit candidates sweeping in |
| Union-Find clustering | Core | Merge by shared funder, shared sweep target |
| Common-input-ownership heuristic (Bitcoin) | Core (BTC) | Inputs of one transaction share an owner |
| Funder / activation linkage | Core | Tron activation source, EVM gas funder |
| Label propagation | Core | A label spreads across the cluster |
| Noisy-OR confidence scoring | Core | `1 − Π(1 − wᵢ)` |
| **Change-address heuristic (Bitcoin)** | **Add-on** | Output is change if it is a fresh address of the same script type as the inputs and the other output is a round amount |
| **Recency decay** | **Add-on** | `w_eff = w · exp(−Δdays / τ)`, with τ set per source (public label 180 d, probe 30 d, federated 365 d) |
| **Mixer detection** | **Add-on** | Score from: equal-denomination deposits, many unrelated depositors, withdrawals to fresh addresses, no direct value linkage. Classify as mixer above a threshold and report "uncertain" |
| **Isotonic calibration** | **Add-on** | Map raw scores to observed accuracy using ground truth (also see Task 16) |

```
detect_sweeps(addr, window W, ratio 0.95):
    for t_in in incoming(addr):
        outs = outgoing(addr, t_in.time, t_in.time + W)
        if sum(outs) >= ratio * t_in.amount and distinct_targets(outs) == 1:
            record(addr → target, delay = outs[0].time − t_in.time)
deposit_candidate(addr) = count(sweeps to same target) >= N
hub_candidate(target)   = distinct(deposit_candidates sweeping to target) >= M
```

**Added requirements (add-on)**
- FR-5.10 Detect mixers by behaviour even when not in the label list; show a clear "probabilistic" tag.
- FR-5.11 Apply recency decay so stale labels lose weight.
- FR-5.12 Apply the BTC change-address heuristic before clustering outputs.
- FR-5.13 Calibrate the confidence output against ground truth.

**Added route:** `GET /nodes/{id}/mixer-score`
**Added table columns:** `labels.last_confirmed_at`, `labels.decay_tau_days`; table `calibration_maps` (id, method, bins_json, fitted_on, created_at)
**Added UI:** `MixerBadge` ("Likely mixer, uncertain"), `LabelAgeIndicator`

### Takeaways for Task 6
- A hub candidate with sweep evidence is the join key for the probe map.
- The scorer already accepts any new signal as a (name, weight, evidence) triple.

---

## TASK 6: Probing / Bait Wallet Module (simulated, honest)

### Functional Requirements
- FR-6.1 Maintain a per-exchange hot-wallet map built from probe results.
- FR-6.2 Record probe runs: exchange, probe deposit address, amount, sweep destination, delay, date.
- FR-6.3 Aggregate probes into hot-wallet clusters with a "matching sweeps" count.
- FR-6.4 Expose a hub match query used by the attribution engine.
- FR-6.5 Provide a simulated probe runner for the demo; real probing hooks are interface only.
- FR-6.6 Flag every probe record as `demo_seed` or `live_probe`.

### Libraries
pandas, APScheduler (periodic re-probe in the production design), pydantic.

### Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/probes/exchanges` | Exchanges covered, last probe date, hot-wallet count |
| GET | `/probes/exchanges/{id}/hotwallets` | Cluster details |
| POST | `/probes/run` | Run simulated probe (admin) |
| GET | `/probes/match?chain=&address=` | Is this hub in a probe cluster? Returns entity, sweep count |
| POST | `/probes/import` | Import seed CSV |

### Controllers / Services
`ProbeService` (`record_probe`, `aggregate`, `match_hub`), `SimulatedProbeRunner` (generates deposit addresses on a synthetic exchange and follows the sweep), `ProbeSeedLoader`.

### Data Tables

| Table | Columns |
|---|---|
| `probe_runs` | id, entity_id, chain, deposit_address, amount, swept_to, sweep_delay_s, run_at, mode (`demo_seed/simulated/live`) |
| `hotwallet_clusters` | id, entity_id, chain, hub_address, matching_sweeps, first_seen, last_confirmed |

### UI Components
`ProbeMapPage` (exchange list with coverage and freshness), `ProbeLogTable`, `ProbeSimulatorButton`, `DemoSeedBanner`.

### Decisions
- For the prototype, seed from public exchange tags plus synthetic exchanges. The pitch states the production version uses LEA-owned accounts and needs legal approval.
- Freshness matters: show `last_confirmed` so stale hot wallets lose weight.

### Implementation Steps
1. Create the tables and seed 3 exchanges.
2. Implement `match_hub`.
3. Plug the match into `ConfidenceScorer` as the probe signal.
4. Build the probe map UI and simulator.
5. Test: Hard preset reaches ≥ 0.9 once the probe signal fires.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Sweep-target aggregation | Core | Group probe sweeps by destination → hot-wallet cluster |
| Match-count weighting | Core | More matching sweeps → higher weight |
| **Freshness scoring** | **Add-on** | `freshness = exp(−days_since_confirmed / τ)` |

**Probe signal weight:** `w_probe = 0.80 · (1 − exp(−matching_sweeps / k)) · freshness` with `k ≈ 10`. 38 sweeps give near the full weight; 2 sweeps give little.

**Added requirement (add-on):** FR-6.7 Show the freshness of every hot-wallet cluster and flag clusters not confirmed for more than 60 days as "stale".
**Added UI:** `FreshnessBar` on the probe map

### Takeaways for Task 7
- When confidence is still under the threshold, there must be a second route to certainty: ask the VASPs.
- Verified answers need to write back into `labels` with `source='verified'`.

---

## TASK 7: Federated VASP Lookup, Mock VASPs and Routing

### Functional Requirements
- FR-7.1 Maintain a VASP directory: country, registration status, channel, avg response time, request format.
- FR-7.2 Send a privacy-preserving "is this address yours?" query to all relevant VASPs (hashed addresses).
- FR-7.3 Collect replies asynchronously; update attribution to `confirmed` on a positive reply.
- FR-7.4 Write confirmed answers back as verified labels.
- FR-7.5 Choose the routing channel: direct portal, stablecoin issuer freeze, FIU/legal escalation for non-responsive foreign VASPs.
- FR-7.6 Maintain a responsiveness score per VASP.

### Libraries
httpx (async fan-out), hashlib/hmac, Celery, FastAPI (mock VASPs).

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/federated-lookup` | Fan out the query |
| GET | `/cases/{id}/federated-lookup` | Replies so far |
| GET | `/vasps` | Directory |
| GET | `/vasps/{id}/stats` | Responsiveness history |
| POST | `/cases/{id}/route` | Compute channel recommendation |

Mock VASP service: `POST /vasp/{name}/lookup` (body: salted hash set → `{match: bool}` after a random delay).

### Controllers / Services
`FederatedLookupService` (hash, fan-out, collect), `VaspDirectoryService`, `RoutingService` (rules table: confirmed VASP in India → portal; USDT → add issuer; foreign non-responsive → escalation), `ResponsivenessTracker`.

### Data Tables

| Table | Columns |
|---|---|
| `vasps` | id, name, country, registered_fiu (bool), channel, request_format, avg_response_s, response_rate |
| `federated_queries` | id, case_id, query_hash, sent_at |
| `federated_replies` | id, query_id, vasp_id, match, received_at, raw_json |
| `routing_decisions` | id, case_id, target_vasp_id, channel, issuer_target, reason_json |

### UI Components
`FederatedLookupPanel` (per-VASP status chips: pending, yes, no, timeout), `VaspDirectoryTable`, `RoutingRecommendationCard` (channel + reason), `ResponsivenessBadge`.

### Decisions
- Hashing uses a per-query salt, so a VASP learns nothing about addresses it doesn't hold. Describe full private set intersection as the production upgrade.
- 4-5 mock VASPs with distinct response delays make the live demo feel real.
- A positive reply sets `attributions.status='confirmed'` and bumps confidence to 0.95+.

### Implementation Steps
1. Mock VASP app and directory seed.
2. Fan-out/collect with timeouts.
3. Write-back to labels and attributions.
4. Routing rules and UI.
5. Test: confirm on the Hard preset; verify no raw addresses are sent for non-matching VASPs.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Salted hashing (HMAC) | Core | Per-query salt; VASPs learn nothing about addresses they don't hold |
| Rule-based routing table | Core | Picks portal, issuer freeze, or escalation |
| Timeout + retry with backoff | Core | Handles slow or silent VASPs |
| **EWMA responsiveness score** | **Add-on** | `r_t = α·x_t + (1 − α)·r_(t−1)` with `x_t = 1 / (1 + response_time / target)`, α = 0.3 |
| **Private Set Intersection (ECDH-based)** | **Add-on (production upgrade)** | Client sends `H(addr)^a`; the VASP returns `H(addr)^(ab)` and its own set `H(x)^b`; the client raises that to `a`, then intersects. Neither side sees the other's non-matching items |

**Added requirements (add-on)**
- FR-7.7 Rank VASPs in the routing recommendation by responsiveness score.
- FR-7.8 Optional PSI mode for the mock VASPs to demonstrate privacy-preserving matching.

**Added libraries:** `cryptography` or `pynacl` (curve operations for PSI)
**Added routes:** `POST /federated/psi/round1`, `POST /federated/psi/round2` (mock VASP side)
**Added table columns:** `vasps.responsiveness_ewma`, `vasps.last_updated`
**Added UI:** `ResponsivenessSparkline` per VASP, `PrivacyModeToggle` (Hash / PSI)

### Takeaways for Task 8
- The system now has a confirmed target VASP, a deposit address, and a routing channel.
- The next question is *how much* to freeze, which needs value tracking per case.

---

## TASK 8: Dye Pack (taint tracking)

### Functional Requirements
- FR-8.1 Create a coloured lot for each victim transaction (case, amount, time).
- FR-8.2 Propagate through every transfer using selectable methods: **Haircut** (default), **FIFO**, optional **Poison**.
- FR-8.3 Maintain a per-address breakdown: balance by case plus uncoloured remainder.
- FR-8.4 Handle merges of multiple cases at one address.
- FR-8.5 Apply a dilution cutoff (default 2%) below which a trail is dropped.
- FR-8.6 Carry colour across bridges/swaps; mark mixer outputs as uncertain.
- FR-8.7 Output at the target deposit address: per-case traceable amount, min/max range (FIFO vs haircut), and the untraceable amount.
- FR-8.8 Link cases that converge on the same address into a gang-level case while keeping per-case amounts separate.
- FR-8.9 Flag when traceable value exceeds the reported loss (possible unreported victims).

### Libraries
`decimal`, networkx (topological order), pandas, hypothesis (property tests), Recharts (UI).

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/taint` | Run taint (method, cutoff) |
| GET | `/cases/{id}/taint/summary` | Per-case amounts at the target address |
| GET | `/addresses/{chain}/{addr}/taint` | Breakdown at any node |
| GET | `/gang-cases/{id}` | Linked cases, shared addresses, totals |
| POST | `/gang-cases/{id}/split-preview` | Restitution split preview |

### Controllers / Services
`TaintEngine` (processes transfers in time order, updates ledgers), `TaintMethods` (strategy classes: `Haircut`, `Fifo`, `Poison`), `GangLinker` (merge cases that converge), `TaintReport`.

### Data Tables

| Table | Columns |
|---|---|
| `taint_lots` | id, case_id, origin_tx, origin_amount, created_at |
| `taint_balances` | id, run_id, chain, address, case_id, amount, method, as_of_time |
| `taint_runs` | id, case_id, method, cutoff, created_at |
| `gang_cases` | id, name, created_at |
| `gang_case_members` | gang_case_id, case_id |

### UI Components
`TaintFlowOverlay` (edge thickness/colour per case on the graph), `TaintPieChart` (per-case share with a grey "not traceable" slice), `MinMaxRangeBar` (FIFO vs haircut), `GangCaseCard`, `CaseColorLegend`, `UnreportedVictimAlert`.

### Decisions
- Conservation of value is an invariant: coloured + uncoloured = total balance at every address.
- Show the min/max range, not a single number. Say plainly that taint on account-based chains is a methodological convention.
- Taint is reliable up to the deposit address; the UI greys out anything after the sweep.

### Implementation Steps
1. Implement the ledger and `Haircut`, with unit tests.
2. Add `Fifo`, then compare ranges.
3. Multi-case merge test on the Multi-victim preset (expected: 14,000 traceable, 9,000 not).
4. Property tests: conservation holds for random graphs.
5. UI: coloured flows and the pie.
6. Gang linking.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Time-ordered ledger update | Core | Process transfers by timestamp so merges and splits apply in the right order |
| **Haircut propagation** | Core | See pseudocode |
| **FIFO propagation** | Core | Oldest lots leave first; gives the other end of the min/max range |
| Dilution cutoff | Core | Drop trails < 2% |
| Value-conservation invariant | Core | coloured + uncoloured = balance at every address |
| Union-Find on shared addresses | Core | Links cases that converge |
| **Poison propagation** | **Add-on** | Any mixing taints the whole output (upper bound) |
| **Unreported-victim estimate** | **Add-on** | `max(0, traceable_total − Σ reported_losses)` |
| **Wallet-farm detection** | **Add-on** | See below |
| **Operator fingerprinting** | **Add-on** | See below |
| **Case-collision detection** | **Add-on** | See below |
| **Louvain community detection** | **Add-on** | See below |

```
haircut(S → R, amount a):
    for each case c in S.taint:
        moved = (S.taint[c] / S.balance) * a
        S.taint[c] -= moved;  R.taint[c] += moved
    S.balance -= a;  R.balance += a
```

### Gang-level intelligence (add-ons)

**Wallet-farm detection.** Group addresses by funder. Flag a farm when ≥ n addresses (say 10) share a funder, were first seen within a short window Δ, and have low age and low tx count. Add all members to the gang cluster.

**Operator fingerprinting.** For each case, build a feature vector: 24-bin activity-hour histogram, median fee/gas settings, batch-size distribution, median delay between hops, one-hot of preferred bridges and exits. Compare two cases by cosine similarity; propose a link above a threshold. This links cases that share no address. It is a suggestion only, and the officer decides.

**Case-collision detection.** Compute salted hashes of cluster IDs for each case. Intersect across cases belonging to different units. If two units have overlapping clusters, notify both officers without revealing case details.

**Louvain community detection.** Build a weighted undirected graph of cases and shared addresses, run `networkx.algorithms.community.louvain_communities`, and show the sub-networks as candidate gang structures.

**Added requirements (add-on)**
- FR-8.10 Show an unreported-victim estimate with a clear "possible, unverified" label.
- FR-8.11 Detect wallet farms and add them to the gang case after officer approval.
- FR-8.12 Suggest links between cases by operator similarity.
- FR-8.13 Notify officers of overlapping clusters across units.
- FR-8.14 Display community structure for large gang cases.

**Added routes**

| Method | Route | Purpose |
|---|---|---|
| GET | `/cases/{id}/wallet-farms` | Detected farms |
| GET | `/cases/{id}/similar-operators` | Ranked similar cases with scores |
| GET | `/collisions` | Collisions visible to the current officer |
| POST | `/collisions/{cid}/open-room` | Open a secure channel between the two units |
| GET | `/gang-cases/{id}/communities` | Louvain output |

**Added tables**

| Table | Columns |
|---|---|
| `wallet_farms` | id, case_id, funder_address, member_count, first_seen_window, status |
| `operator_fingerprints` | case_id, vector_json, computed_at |
| `case_similarity` | case_a, case_b, cosine, features_json |
| `case_collisions` | id, case_a, case_b, hashed_cluster_id, status, created_at |

**Added UI:** `WalletFarmCluster` (grouped nodes), `SimilarOperatorsCard` (percentage plus feature comparison), `CollisionNotice`, `GangNetworkView` (colour by community)

### Takeaways for Task 9
- For the target address we have `traceable_amount`, `total_balance`, and per-case amounts.
- Blast Radius needs those plus depositor-count and sweep status.

---

## TASK 9: Blast Radius (impact preview)

### Functional Requirements
- FR-9.1 Determine the freeze target level: deposit address/account, never the hot wallet.
- FR-9.2 Compute: traceable share, number of other depositors, whether funds were already swept, time since arrival, address type (personal/merchant/pooled).
- FR-9.3 Produce an impact level (Low/Medium/High) and a recommended action: exact-amount freeze, account hold pending review, or review request only.
- FR-9.4 Show "freeze X of Y" with an estimate disclaimer (only the VASP knows the true balance).
- FR-9.5 Block "Propose Freeze" if the target is a hub or if funds have been swept.

### Libraries
pandas, Recharts, pydantic.

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/blast-radius` | Compute and store |
| GET | `/cases/{id}/blast-radius` | Latest result |

### Controllers / Services
`BlastRadiusService` (gathers metrics from taint, trace, and address profile), `ImpactClassifier` (rule table), `RecommendationBuilder`.

### Impact rules (starting values)

| Condition | Impact |
|---|---|
| Traceable share ≥ 50% and depositors ≤ 3 and not swept | Low |
| Traceable share 10-50% or depositors 4-20 | Medium |
| Pooled/merchant address, depositors > 20, or share < 10% | High |
| Swept to hub | Blocked: switch to freeze at the next live address |

### Data Tables

| Table | Columns |
|---|---|
| `blast_radius_reports` | id, case_id, target_chain, target_address, traceable_amount, est_balance, share_pct, other_depositors, swept (bool), minutes_since_arrival, address_type, impact_level, recommendation, created_at |

### UI Components
`ImpactGauge` (Low/Med/High), `FreezeSlider` ("Freeze X of Y, Z% impact"), `MetricGrid` (depositors, swept, time since arrival), `RecommendationBanner`, `EstimateDisclaimer`.

### Decisions
- Rules are transparent and tunable in a config file, so a judge can read why High was chosen.
- `swept = true` is a hard block that changes the recommendation, not just a warning.

### Implementation Steps
1. Metric collection from earlier tasks.
2. Rule engine and unit tests for each row of the table.
3. UI gauge and slider.
4. Hook the output into the draft freeze request (Task 10).

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Rule-based impact classifier | Core | Transparent Low / Medium / High table |
| Distinct-depositor counting | Core | Unique senders to the target address |
| Swept-funds check | Core | Balance vs outflow after arrival |
| **Address-type heuristic** | **Add-on** | Features: unique sender count, sender diversity (entropy), average deposit size, inflow regularity. Rules (or a small logistic model) classify personal / merchant / pooled |

**Added requirement (add-on):** FR-9.6 Classify the target address type and use it in the impact rule (pooled → High).
**Added table column:** `blast_radius_reports.address_type_confidence`
**Added UI:** `AddressTypeChip` with an explanation popover

### Takeaways for Task 10
- A complete "freeze package" now exists: target VASP, target address, amount per case, impact, and recommendation.
- The workflow must record who proposed, who approved, and when.

---

## TASK 10: Two-Key Freeze Workflow and Hash-Chained Audit Log

### Functional Requirements
- FR-10.1 Create a freeze/disclosure request draft from the freeze package.
- FR-10.2 State machine: `DRAFT → PROPOSED → APPROVED | REJECTED | RETURNED → SENT → ACKNOWLEDGED → FROZEN | DECLINED → EXPIRED | RELEASED`.
- FR-10.3 Investigator proposes with a reason; a different Supervisor approves with TOTP 2FA.
- FR-10.4 The same user can never hold both keys.
- FR-10.5 Tiered rule: Low impact → one approval plus post-review; Medium/High → both keys and written justification.
- FR-10.6 Golden-hour mode: a time-limited hold goes out on one key, auto-lapses unless the second key confirms within N minutes.
- FR-10.7 On approval, send through the (mock) SAHYOG client to the VASP, and to the issuer if routed.
- FR-10.8 Track status, expiry, reminders, and release.
- FR-10.9 Every action writes to an append-only audit log where each row stores the previous row's hash.
- FR-10.10 Audit chain verification endpoint detects tampering.

### Libraries
`transitions` (or a hand-written state machine), pyotp, hashlib, Celery beat (expiry/lapse timers), WebSocket notifications.

### Routes

| Method | Route | Purpose | Role |
|---|---|---|---|
| POST | `/cases/{id}/requests` | Create draft from package | Investigator |
| POST | `/requests/{rid}/propose` | Submit with reason | Investigator |
| POST | `/requests/{rid}/approve` | Approve (needs 2FA) | Supervisor |
| POST | `/requests/{rid}/reject` | Reject with reason | Supervisor |
| POST | `/requests/{rid}/return` | Return for more evidence | Supervisor |
| POST | `/requests/{rid}/golden-hour` | One-key hold | Investigator |
| POST | `/requests/{rid}/confirm` | Second key on golden-hour hold | Supervisor |
| POST | `/requests/{rid}/release` | Release freeze | Supervisor |
| GET | `/requests/{rid}` | Detail and timeline | any |
| GET | `/audit?entity=&id=` | Audit entries | Supervisor/Admin |
| GET | `/audit/verify` | Verify hash chain | Admin |
| WS | `/ws/notifications` | Approval pings | any |

### Controllers / Services
`RequestService` (draft/build payload), `ApprovalService` (rule checks: separation of duties, tier rules, 2FA), `RequestStateMachine`, `DispatchService` (calls `SahyogClient`), `ExpiryScheduler`, `AuditService` (`append()`, `verify_chain()`).

### Data Tables

| Table | Columns |
|---|---|
| `freeze_requests` | id, case_id, kind (`FREEZE/DISCLOSURE/BOTH`), target_vasp_id, target_address, amount_by_case_json, issuer_target, impact_level, state, proposer_id, approver_id, reason, golden_hour (bool), expires_at, sahyog_request_id |
| `request_events` | id, request_id, from_state, to_state, actor_id, note, at |
| `audit_log` | id (bigserial), actor_id, action, entity, entity_id, payload_hash, prev_hash, entry_hash, at |
| `notifications` | id, user_id, request_id, kind, read_at |

### UI Components
`FreezePackageSummary` (target, Dye Pack table, Blast Radius, evidence), `ProposeDialog`, `ApprovalCard` (approve/reject/return, 2FA prompt), `RequestTimeline` (states with timestamps), `GoldenHourBanner` with countdown, `AuditLogViewer`, `ChainIntegrityBadge`, `NotificationBell`.

### Decisions
- `entry_hash = SHA256(prev_hash + canonical_json(entry))`; a nightly job and an on-demand endpoint verify the chain.
- Role separation is enforced in the service layer, not just the UI.
- Map the tiers to statutory freeze powers (BNSS Section 106) with a legal expert before production.
- Mock SAHYOG returns scripted outcomes (frozen / declined / delayed) to demo every state.

### Implementation Steps
1. Implement `AuditService` and replace the Task 1 stub everywhere.
2. State machine with unit tests (every illegal transition rejected).
3. Approval rules (same user, 2FA, tiers).
4. Dispatch to mock SAHYOG; status polling.
5. Golden-hour timers.
6. UI: propose and approval screens; open two browsers for the demo.
7. Tamper test: modify a row in the DB and show `verify` failing.

### Algorithms (Core)

| Algorithm | Tier | Detail |
|---|---|---|
| Finite state machine | Core | Only listed transitions are allowed; illegal transitions raise errors |
| Separation-of-duties check | Core | `proposer_id != approver_id` enforced in the service layer |
| TOTP (RFC 6238) | Core | HMAC-SHA1, 30-second step, ±1 step tolerance |
| Hash-chained log | Core | `entry_hash = SHA256(prev_hash + canonical_json(entry))` |
| Chain verification scan | Core | Recompute every hash, report the first mismatch |
| Timer scheduling | Core | Celery ETA tasks for golden-hour lapse and request expiry |

### Takeaways for Task 11
- Every approved request has a complete record: trace, labels, taint figures, impact, approvals, and VASP responses.
- All of it needs to be exportable as one tamper-evident bundle.

---

## TASK 11: Reports and Evidence Package

### Functional Requirements
- FR-11.1 Generate an investigation-ready PDF: case summary, transaction path with hashes and timestamps, labels used (with source and date), confidence reasoning, Dye Pack table, Blast Radius, approvals, VASP responses.
- FR-11.2 Snapshot the labels *as of the report date* so later label changes do not alter old reports.
- FR-11.3 Compute a SHA-256 of the evidence bundle and embed it as a QR code linking to a public verification page.
- FR-11.4 Generate a Section 63 BSA certificate template (placeholders for the certifying officer; legal review required).
- FR-11.5 Export machine-readable JSON of the same bundle.
- FR-11.6 Case-wise analytics dashboard: counts, amounts traced, frozen amounts, time-to-attribution, VASP response stats.

### Libraries
reportlab (or WeasyPrint + Jinja2), qrcode, Pillow, Jinja2; Recharts for analytics.

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/reports` | Build report and bundle |
| GET | `/reports/{rid}/pdf` | Download PDF |
| GET | `/reports/{rid}/bundle.json` | Evidence bundle |
| GET | `/verify/{hash}` | Public verification page data |
| GET | `/analytics/overview` | Dashboard metrics |
| GET | `/analytics/vasps` | VASP response analytics |

### Controllers / Services
`ReportBuilder`, `EvidenceSnapshotService` (freezes labels, graph, and taint output into immutable rows), `HashSealService`, `CertificateGenerator`, `AnalyticsService`.

### Data Tables

| Table | Columns |
|---|---|
| `reports` | id, case_id, version, bundle_hash, pdf_path, created_by, created_at |
| `evidence_snapshots` | id, report_id, kind (`labels/graph/taint/approvals`), payload_json |
| `analytics_daily` (optional materialized view) | date, cases_opened, avg_time_to_attribution_s, amount_traced, amount_frozen |

### UI Components
`ReportPreview`, `GenerateReportButton`, `VerificationPage` (shows "Verified, unchanged since …"), `AnalyticsDashboard` (KPI tiles, time-to-attribution chart, VASP response bars), `ExportMenu`.

### Decisions
- The report states the label's source and as-of date next to every attribution, so cross-examination has an answer.
- Time-to-attribution is the headline KPI ("minutes, not days").
- The certificate is a template with an explicit "needs legal review" note.

### Implementation Steps
1. Snapshot service and bundle hashing.
2. PDF template; add QR.
3. Verification route and page.
4. Analytics queries and dashboard.
5. Test: change one byte in the bundle → verification fails.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Canonical JSON serialization | Core | Sorted keys, no whitespace, Decimals as strings, so the same data always gives the same hash |
| SHA-256 bundle hashing | Core | Report fingerprint |
| QR encoding | Core | Links the printed report to the verification page |
| **Merkle tree over evidence items** | **Add-on** | Leaves = SHA-256 of each evidence item; the root is printed in the QR. Lets you prove one item belonged to the bundle without revealing the rest |

**Added requirement (add-on):** FR-11.7 Provide an inclusion proof for any single evidence item (for example, one label snapshot) against the published root.
**Added route:** `GET /reports/{rid}/proof/{item_id}` → `{leaf, path, root}`
**Added table:** `evidence_items` (id, report_id, kind, leaf_hash, position)
**Added UI:** `ProofViewer` on the verification page ("Item verified in bundle")

### Takeaways for Task 12
- All the data is available; the dashboard must now tie it together in one case workspace.

---

## TASK 12: Case Workspace Dashboard (integration of the UI)

### Functional Requirements
- FR-12.1 One case page with a stepper: Trace → Attribution → Taint → Impact → Approval → Routing → Report.
- FR-12.2 Graph is the central canvas; side panels change by step.
- FR-12.3 Live updates through WebSocket.
- FR-12.4 Time-machine replay: scrub through the trace chronologically.
- FR-12.5 Role-aware actions (investigator vs supervisor views).
- FR-12.6 Plain-language "explain this" on every number.
- FR-12.7 Multilingual shell (English plus Hindi) for the key labels.

### Libraries
Cytoscape.js (+ dagre), react-i18next, Recharts, Framer Motion (light transitions), TanStack Query.

### Routes (frontend)

| Path | Page |
|---|---|
| `/login` | Login |
| `/cases` | Case list |
| `/cases/:id` | Case workspace (tabs by step) |
| `/cases/:id/requests/:rid` | Request detail |
| `/gang/:id` | Gang-level view |
| `/probes` | Probe map |
| `/vasps` | VASP directory |
| `/analytics` | Dashboard |
| `/audit` | Audit viewer |
| `/dev/scenarios` | Scenario tools (dev) |

### Controllers
Frontend only; reuse earlier endpoints. Add one aggregator endpoint `GET /cases/{id}/workspace` returning case, latest trace, attribution, taint summary, blast radius, request state in one call to cut round trips.

### Data Tables
No new tables. Optional `ui_preferences (user_id, lang, graph_layout)`.

### UI Components
`CaseStepper`, `GraphCanvas`, `TimeScrubber` (replays edges by timestamp), `SidePanelRouter`, `ExplainPopover`, `LanguageToggle`, `EmptyState`/`ErrorState`/`Skeleton` components.

### Decisions
- Graph interactions: click node → inspector; hover edge → amount/time; filter by case colour.
- The time scrubber is client-side playback over already-loaded edges (cheap but impressive).
- The workspace shows what is simulated using `SimulatedBadge` wherever applicable.

### Implementation Steps
1. Aggregator endpoint.
2. Stepper plus panel routing.
3. Time scrubber.
4. i18n for main labels.
5. Polish states (loading, empty, error).

### Takeaways for Task 13
- The graph canvas can render an additional overlay layer; Forecast and Fence/Sleeper features will plug into it.

---

## TASK 13: Forecast Cone

### Functional Requirements
- FR-13.1 Given the current frontier nodes of a trace, estimate probabilities for the next hop and the likely exit entity.
- FR-13.2 Show probable exit entity with a time estimate (e.g., "76% via Exchange X within 40 minutes").
- FR-13.3 Display forecast as fading forward edges on the graph.
- FR-13.4 Always label the model "trained on simulated paths" in the prototype.
- FR-13.5 Use forecasts to pre-stage draft requests (not to send them).

### Libraries
numpy, scikit-learn (optional logistic model), pandas.

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/cases/{id}/forecast` | Run Monte Carlo simulation |
| GET | `/cases/{id}/forecast` | Latest result |
| POST | `/dev/forecast/train` | Train from scenario set |

### Controllers / Services
`ForecastService`, `MarkovModel` (states: address-role types and entity classes; transitions from historical paths), `MonteCarloRunner` (e.g., 1,000 rollouts), `DelayModel` (empirical delay distribution per transition).

### Data Tables

| Table | Columns |
|---|---|
| `forecast_models` | id, version, trained_on, transition_matrix_json, delay_params_json, created_at |
| `forecasts` | id, case_id, model_id, result_json (exit_entity_probs, eta_quantiles), created_at |

### UI Components
`ForecastOverlay` (translucent branches with probability labels), `ExitProbabilityBar`, `ETAChip`, `ModelDisclaimer`.

### Decisions
- Markov chain, not a GNN: explainable and fast to build.
- Probabilities are shown as ranges (10th-90th percentile ETA).
- Never auto-send anything on a forecast.

### Implementation Steps
1. Extract path sequences from generated scenarios.
2. Fit the transition matrix; fit delay distributions.
3. Monte Carlo rollout from a frontier.
4. Draw the overlay.
5. Document the evaluation: top-1 exit accuracy on held-out synthetic paths.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Markov chain | Core | Transition matrix over node roles and entity classes |
| Monte Carlo simulation | Core | 1,000 rollouts from the frontier |
| Empirical delay quantiles | Core | Per-transition delay distribution → ETA range |
| **Cash-out ranking** | **Add-on** | Per-gang exit preference |

**Cash-out ranking:** for a gang with exit history, `P_gang(exit = e) = (count_e + 1) / (total + |E|)` (Laplace smoothing, with recent exits weighted higher). Blend with the global Markov probability: `P = λ·P_gang + (1 − λ)·P_global`, where λ grows with the number of observed exits.

**Added requirement (add-on):** FR-13.6 Re-rank forecast exits by the gang's history once ≥ 3 prior exits exist.
**Added table:** `gang_exit_history` (gang_case_id, entity_id, exit_time, amount)
**Added UI:** `GangPreferenceBar` showing the global vs gang-adjusted probability

### Takeaways for Task 14
- Overlays on the graph work; watch features can reuse the live-update channel.

---

## TASK 14: Wallet Fence (Watched Wallets Panel)

### Functional Requirements
- FR-14.1 Each case has a watchlist (the "fence") of scammer-side addresses.
- FR-14.2 When the trace or clustering finds a linked address, propose it as a card: **Add / Ignore**.
- FR-14.3 For inbound crossings from an outside address, offer: **Add to fence / Tag as victim / Ignore**.
- FR-14.4 Monitor all watched addresses; when funds reach a labeled VASP, raise an alert and pre-draft the request.
- FR-14.5 Show the fence boundary (dashed outline) on the graph.

### Libraries
Cytoscape compound nodes, Celery beat (polling monitor), WebSocket.

### Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/cases/{id}/fence` | Members and pending proposals |
| POST | `/cases/{id}/fence/proposals/{pid}/decide` | Add / tag victim / ignore |
| POST | `/cases/{id}/fence/members` | Manual add |
| GET | `/alerts?case_id=` | Alerts |
| POST | `/alerts/{id}/ack` | Acknowledge |

### Controllers / Services
`FenceService`, `FenceProposalGenerator` (runs after each trace and monitor poll), `WatchMonitor` (polls watched addresses), `AlertService`.

### Data Tables

| Table | Columns |
|---|---|
| `fence_members` | id, case_id, chain, address, added_by, added_at, source |
| `fence_proposals` | id, case_id, chain, address, direction (`inbound/outbound`), reason_json, status |
| `alerts` | id, case_id, kind (`FENCE_CROSS/DORMANT_WAKE/VASP_REACHED`), payload_json, created_at, ack_by |

### UI Components
`FencePanel`, `ProposalCard` (the accept/reject moment), `FenceBoundary` (compound node), `AlertToast`, `AlertList`.

### Decisions
- Presented as the officer-approved growing watchlist; no claim of a new algorithm.
- Victim addresses are never added to the fence.
- Polling interval configurable; in replay mode alerts are triggered by scripted events.

### Implementation Steps
1. Tables and proposal generator.
2. Decide endpoint and UI cards.
3. Monitor job and alert creation.
4. Graph boundary rendering.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Fence expansion rule | Core | Score a candidate: `0.4·shared_funder + 0.3·flow_share_from_fence + 0.3·sweeps_with_fence`; propose when score > 0.5 |
| Polling with change detection | Core | Store a per-address cursor (last block/time) and fetch only newer transfers |

### Takeaways for Task 15
- The alert pipeline exists; the Sleeper-Cell is one more alert type.

---

## TASK 15: Sleeper-Cell Detector

### Functional Requirements
- FR-15.1 For each watched/tainted address, compute dormancy periods.
- FR-15.2 Alert when a dormant tainted address moves funds after ≥ N days (default 30).
- FR-15.3 Show a timeline with the quiet period and the awakening spike.
- FR-15.4 Automatically re-run trace and taint from the awakened address.

### Libraries
pandas, Recharts, Celery beat.

### Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/addresses/{chain}/{addr}/activity` | Activity timeline |
| GET | `/cases/{id}/dormancy` | Dormant addresses and thresholds |
| POST | `/cases/{id}/dormancy/threshold` | Set N days |

### Controllers / Services
`DormancyService` (gap detection), `AwakeningDetector` (runs in `WatchMonitor`), `RetraceTrigger`.

### Data Tables

| Table | Columns |
|---|---|
| `dormancy_state` | chain, address, last_active, dormant_since, threshold_days, tainted_amount |
| `alerts` (reuse) | kind = `DORMANT_WAKE` |

### UI Components
`ActivityTimeline` (flat line then spike), `DormantList`, `AwakeningAlert`.

### Decisions
- Use real Tron/Ethereum history for the demo address and a synthetic one for the guaranteed alert.
- Re-trace is queued, not automatic freeze.

### Implementation Steps
1. Gap detector with tests.
2. Alert hook.
3. Timeline chart.
4. Retrace trigger.

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Gap detection | Core | `now − last_active > N days` marks an address dormant |
| **Awakening anomaly (z-score)** | **Add-on** | `z = (ln(amount) − μ_hist) / σ_hist` using the address's own history. Alert if the gap ≥ N and (z > 2 or amount ≥ 10% of tainted balance). Suppresses dust wake-ups |

**Added requirement (add-on):** FR-15.5 Suppress awakening alerts for trivial amounts and explain the alert reason (gap length, z-score, share of tainted funds).
**Added UI:** `AlertReasonChips` ("94 days dormant", "z = 3.1", "40% of tainted balance")

### Takeaways for Task 16
- All functional modules exist. What remains is measurement, hardening, and packaging.

---

## TASK 16: Hardening, Calibration, Testing, Submission Package

### Functional Requirements
- FR-16.1 Attribution accuracy and calibration report on the synthetic set plus 30-50 real addresses with public labels.
- FR-16.2 Load test: 1,000 simulated cases through the queue; report throughput and p95 trace time.
- FR-16.3 Failure handling: API outage → cache; rate limit → queued retry; unsupported chain → clear result.
- FR-16.4 Security checks: RBAC tests, 2FA on approvals, secrets in env, input validation.
- FR-16.5 One-command startup and a demo script.

### Libraries
pytest, locust (or k6), scikit-learn (`calibration_curve`), matplotlib, Playwright (end-to-end), ruff/black, GitHub Actions.

### Routes
`GET /health`, `GET /metrics` (Prometheus format, optional), `GET /version`.

### Controllers / Services
`CalibrationJob` (bins predicted confidence vs actual hit rate and refits weights), `BenchmarkRunner`, `HealthService`.

### Data Tables

| Table | Columns |
|---|---|
| `benchmark_runs` | id, dataset, precision, recall, avg_time_to_attribution_s, calibration_json, created_at |

### UI Components
`HealthPage`, `BenchmarkReportPage` (calibration chart, accuracy table).

### Algorithms (Core + Add-on)

| Algorithm | Tier | Detail |
|---|---|---|
| Calibration curve | Core | Bin predicted confidence, compare to observed hit rate |
| **Isotonic regression / Platt scaling** | **Add-on** | Fit a monotonic map from raw score to calibrated probability (`sklearn.isotonic.IsotonicRegression`) |
| **Expected Calibration Error (ECE)** | **Add-on** | `Σ (n_bin / N) · |accuracy_bin − confidence_bin|` |
| **Bootstrap confidence intervals** | **Add-on** | Resample the test set to report error bars on precision/recall |
| Precision / recall / F1 | Core | Attribution quality |

### Test Checklist

| Area | Tests |
|---|---|
| Trace | Reaches known deposit/hub on all 3 presets |
| Attribution | Correct entity on Hard preset; no false positive on noise |
| Dye Pack | Conservation of value; Multi-victim = 14,000 / 9,000 |
| Blast Radius | Each rule row |
| Two-Key | Same user cannot approve; golden-hour lapse; illegal transitions rejected |
| Audit | Tampering is detected |
| Reports | Altered bundle fails verification |
| Replay | Full demo runs with the network disabled |
| E2E | Playwright runs the complete demo flow |

### Submission Package
1. Repository with README and `docker compose up`.
2. 5-6 minute demo video following: complaint → trace → attribution → federated confirm → Dye Pack → Blast Radius → two-key approval → routing → report with QR.
3. Architecture document with the SAHYOG API contract (export OpenAPI) and data model.
4. Slide deck: problem, approach, differentiators, results, roadmap.
5. "Prototype vs production" table (simulated: probing, federated VASPs, SAHYOG; needed for production: LEA exchange accounts, real integration, commercial intelligence feeds, legal approvals).
6. Benchmark and calibration results.

### Decisions
- Report limits openly: attribution is probabilistic, mixers stay partly uncertain, taint on account-based chains is a range, and probing requires approval.

---

## Task Dependency Summary

```
T1 Foundation ─► T2 Chain adapters ─► T3 Synthetic data ─► T4 Trace
                                                              │
                              T5 Attribution ◄────────────────┘
                                   │
                   T6 Probe map ───┤
                   T7 Federated + routing
                                   │
                              T8 Dye Pack ─► T9 Blast Radius ─► T10 Two-Key + Audit
                                                                      │
                                                          T11 Reports & analytics
                                                                      │
                                                          T12 Workspace UI integration
                                                                      │
                                          T13 Forecast ─► T14 Fence ─► T15 Sleeper
                                                                      │
                                                          T16 Hardening & submission
```

**Minimum viable submission:** Tasks 1-12. Tasks 13-15 are add-ons, cut from the bottom if time runs short.

---

## Appendix: Algorithm Index

| Algorithm | Tier | Task |
|---|---|---|
| Address checksum validation | Core | 1 |
| OCR + regex extraction | Add-on | 1 |
| Token bucket, backoff, idempotent upsert | Core | 2 |
| Seeded RNG, Poisson, log-normal sampling | Core | 3 |
| Peel-chain / wallet-farm / mixer / dormancy injectors | Add-on | 3 |
| Best-first trace, pruning, time-window filter, bridge decoding | Core | 4 |
| Swap amount-and-time matching | Add-on | 4 |
| Peel-chain, layering, chain-hopping detection | Add-on | 4 |
| Sweep detection, hub validation | Core | 5 |
| Union-Find clustering, label propagation, funder linkage | Core | 5 |
| Common-input ownership (BTC) | Core | 5 |
| Noisy-OR confidence | Core | 5 |
| Change-address heuristic | Add-on | 5 |
| Recency decay | Add-on | 5 |
| Mixer detection | Add-on | 5 |
| Isotonic calibration | Add-on | 5, 16 |
| Sweep-target aggregation, match-count weighting | Core | 6 |
| Freshness scoring | Add-on | 6 |
| Salted hashing, routing table, retry with backoff | Core | 7 |
| EWMA responsiveness | Add-on | 7 |
| Private Set Intersection | Add-on | 7 |
| Haircut, FIFO, dilution cutoff, conservation check | Core | 8 |
| Poison propagation, unreported-victim estimate | Add-on | 8 |
| Wallet-farm detection, operator fingerprinting | Add-on | 8 |
| Case-collision detection, Louvain communities | Add-on | 8 |
| Rule-based impact classifier, depositor count, swept check | Core | 9 |
| Address-type heuristic | Add-on | 9 |
| State machine, TOTP, separation of duties, hash chain | Core | 10 |
| Canonical JSON, SHA-256, QR | Core | 11 |
| Merkle tree and inclusion proofs | Add-on | 11 |
| Markov chain, Monte Carlo, delay quantiles | Core | 13 |
| Cash-out ranking | Add-on | 13 |
| Fence expansion rule, polling with change detection | Core | 14 |
| Gap detection | Core | 15 |
| Awakening z-score | Add-on | 15 |
| Calibration curve, precision/recall | Core | 16 |
| ECE, bootstrap CIs, Platt scaling | Add-on | 16 |

**Build priority for add-ons:** (1) mixer detection, peel-chain detection, and isotonic calibration, as they directly strengthen attribution and trust; (2) wallet-farm detection, case collision, and unreported-victim estimate, which are gang-level wins and cheap to build; (3) OCR, Merkle proofs, cash-out ranking, and operator fingerprinting; (4) PSI and Louvain, which can stay as roadmap items if time is short.
