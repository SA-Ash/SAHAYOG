import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { CaseGraph, Graph } from "./intelligence";

type Member = { id: string; chain: string; address: string };
type Proposal = Member & {
  direction: string;
  status: string;
  reason_json: Record<string, unknown>;
};
type Alert = {
  id: string;
  kind: string;
  at: string;
  ack_by?: string;
  payload_json: Record<string, unknown>;
};
type Fence = {
  members: Member[];
  proposals: Proposal[];
  settings: { threshold_days: number; poll_seconds: number };
};
type Dormancy = {
  addresses: (Member & {
    last_active?: string;
    dormant_since?: string;
    tainted_balance_json: Record<string, unknown>;
  })[];
};

export function MonitorPanel({
  caseId,
  user,
  graph,
  dormant = false,
  synthetic = false,
}: {
  caseId: string;
  user: { role: string };
  graph: Graph | null;
  dormant?: boolean;
  synthetic?: boolean;
}) {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [address, setAddress] = useState("");
  const [chain, setChain] = useState("tron");
  const [days, setDays] = useState(30);
  const [seconds, setSeconds] = useState(60);
  const fence = useQuery({
    queryKey: ["fence", caseId],
    queryFn: () => api<Fence>(`/cases/${caseId}/fence`),
    refetchInterval: 5000,
  });
  const alerts = useQuery({
    queryKey: ["alerts", caseId],
    queryFn: () => api<Alert[]>(`/alerts?case_id=${caseId}`),
    refetchInterval: 5000,
  });
  const state = useQuery({
    queryKey: ["dormancy", caseId],
    queryFn: () => api<Dormancy>(`/cases/${caseId}/dormancy`),
    refetchInterval: 5000,
  });
  async function act(path: string, body: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, body);
      await client.invalidateQueries();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const members = new Set(
    fence.data?.members.map((m) => `${m.chain}:${m.address}`),
  );
  const fencedGraph = graph && {
    ...graph,
    nodes: [
      ...graph.nodes.map((n) => ({
        data: {
          ...n.data,
          ...(members.has(`${n.data.chain}:${n.data.address}`)
            ? { parent: "fence-boundary" }
            : {}),
        },
      })),
      ...(graph.nodes.some((n) =>
        members.has(`${n.data.chain}:${n.data.address}`),
      )
        ? [
            {
              data: {
                id: "fence-boundary",
                label: "Officer-approved wallet fence",
                chain: "",
                address: "",
                hop: 0,
                role: "fence",
                evidence: [],
              },
            },
          ]
        : []),
    ],
  };
  return (
    <section>
      <h3>{dormant ? "Sleeper activation monitoring" : "Wallet fence"}</h3>
      <p>
        Victim wallets are excluded. Alerts require officer review; requests are
        never sent automatically.
      </p>
      {(error || fence.error || state.error || alerts.error) && (
        <p role="alert" className="error">
          {error || String(fence.error || state.error || alerts.error)}
        </p>
      )}
      {fencedGraph && (
        <CaseGraph graph={fencedGraph} onSelect={() => undefined} />
      )}
      {user.role === "INVESTIGATOR" && (
        <>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(`/cases/${caseId}/fence/members`, { chain, address });
            }}
          >
            <select value={chain} onChange={(e) => setChain(e.target.value)}>
              {["tron", "ethereum", "bnb", "polygon", "bitcoin"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            <input
              aria-label="Fence wallet address"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              required
              placeholder="Scammer-side wallet"
            />
            <button disabled={busy}>Add member</button>
          </form>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(`/cases/${caseId}/dormancy/threshold`, {
                threshold_days: days,
                poll_seconds: seconds,
              });
            }}
          >
            <label>
              Dormancy days
              <input
                type="number"
                min="1"
                max="3650"
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
              />
            </label>
            <label>
              Polling seconds
              <input
                type="number"
                min="10"
                max="86400"
                value={seconds}
                onChange={(e) => setSeconds(Number(e.target.value))}
              />
            </label>
            <button disabled={busy}>Save monitoring settings</button>
          </form>
          <p>
            Current threshold: {fence.data?.settings.threshold_days} days; poll
            every {fence.data?.settings.poll_seconds}s.
          </p>
          <button
            disabled={busy}
            onClick={() => void act(`/cases/${caseId}/fence/poll`, {})}
          >
            Poll now
          </button>
          {synthetic && (
            <button
              disabled={busy}
              onClick={() =>
                void act(`/cases/${caseId}/fence/poll`, {
                  as_of: "2030-01-01T00:00:00Z",
                })
              }
            >
              Replay synthetic events
            </button>
          )}
        </>
      )}
      {!dormant && (
        <>
          <h4>Members</h4>
          {fence.data?.members.map((m) => (
            <p key={m.id}>
              {m.chain}: {m.address}
            </p>
          ))}
          <h4>Pending proposals</h4>
          {fence.data?.proposals
            .filter((p) => p.status === "PENDING")
            .map((p) => (
              <article key={p.id}>
                <p>
                  {p.direction}: {p.chain} {p.address}
                </p>
                <details>
                  <summary>Explain proposal</summary>
                  <pre>{JSON.stringify(p.reason_json, null, 2)}</pre>
                </details>
                {user.role === "INVESTIGATOR" &&
                  [
                    "ADD",
                    "IGNORE",
                    ...(p.direction === "in" ? ["VICTIM"] : []),
                  ].map((decision) => (
                    <button
                      disabled={busy}
                      key={decision}
                      onClick={() =>
                        void act(
                          `/cases/${caseId}/fence/proposals/${p.id}/decide`,
                          { decision },
                        )
                      }
                    >
                      {decision === "VICTIM" ? "Tag as victim" : decision}
                    </button>
                  ))}
              </article>
            ))}
        </>
      )}
      {dormant && (
        <>
          <h4>Activity timelines</h4>
          {state.data?.addresses.map((s) => (
            <Activity key={s.id} state={s} caseId={caseId} />
          ))}
        </>
      )}
      <h4>Alerts</h4>
      <div aria-live="polite">
        {alerts.data?.map((a) => (
          <article key={a.id}>
            <strong>{a.kind}</strong>
            <time> {new Date(a.at).toLocaleString()}</time>
            <details>
              <summary>Reasons and request pre-draft</summary>
              <pre>{JSON.stringify(a.payload_json, null, 2)}</pre>
            </details>
            {a.ack_by ? (
              <span>Acknowledged</span>
            ) : (
              <button
                disabled={busy}
                onClick={() => void act(`/alerts/${a.id}/ack`, {})}
              >
                Acknowledge
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
function Activity({
  state,
  caseId,
}: {
  state: Dormancy["addresses"][number];
  caseId: string;
}) {
  const [open, setOpen] = useState(false);
  const events = useQuery({
    queryKey: ["activity", caseId, state.chain, state.address],
    queryFn: () =>
      api<{
        events: { block_time: string; amount: string; from_addr: string }[];
      }>(
        `/addresses/${state.chain}/${state.address}/activity?case_id=${caseId}`,
      ),
    enabled: open,
  });
  const times = events.data?.events.map((e) => Date.parse(e.block_time)) || [];
  const start = Math.min(...times),
    end = Math.max(...times);
  return (
    <article>
      <button className="secondary" onClick={() => setOpen(!open)}>
        {state.chain}: {state.address}
      </button>
      <p>
        {state.dormant_since
          ? `Dormant since ${state.dormant_since}`
          : `Last active ${state.last_active || "unknown"}`}
      </p>
      {open && (
        <>
          <svg
            viewBox="0 0 600 80"
            role="img"
            aria-label="Wallet activity timeline; gaps show quiet periods"
          >
            <line x1="10" x2="590" y1="60" y2="60" stroke="gray" />
            {events.data?.events.map((e, i) => (
              <line
                key={i}
                x1={10 + ((times[i] - start) / Math.max(1, end - start)) * 580}
                x2={10 + ((times[i] - start) / Math.max(1, end - start)) * 580}
                y1={e.from_addr === state.address ? 10 : 35}
                y2="60"
                stroke={e.from_addr === state.address ? "#b25965" : "#00948c"}
              >
                <title>
                  {e.block_time}: {e.amount} base units
                </title>
              </line>
            ))}
          </svg>
          {events.error && <p role="alert">{String(events.error)}</p>}
          <pre>{JSON.stringify(events.data?.events, null, 2)}</pre>
        </>
      )}
    </article>
  );
}
export function HealthPage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api<Record<string, unknown>>("/health"),
    refetchInterval: 10000,
  });
  const version = useQuery({
    queryKey: ["version"],
    queryFn: () => api<Record<string, unknown>>("/version"),
  });
  return (
    <main>
      <h1>System health</h1>
      {health.error && <p role="alert">{String(health.error)}</p>}
      <pre>{JSON.stringify(health.data, null, 2)}</pre>
      <pre>{JSON.stringify(version.data, null, 2)}</pre>
    </main>
  );
}
export function BenchmarkReportPage() {
  const runs = useQuery({
    queryKey: ["benchmarks"],
    queryFn: () =>
      api<
        {
          id: string;
          dataset: string;
          at: string;
          result_json: Record<string, unknown>;
        }[]
      >("/benchmarks"),
  });
  return (
    <main>
      <h1>Benchmark reports</h1>
      <p>
        Training and held-out evaluation are separated. Public-label lookup
        coverage is distinct from live behavioural attribution accuracy.
      </p>
      {runs.error && <p role="alert">{String(runs.error)}</p>}
      {runs.data?.length === 0 && (
        <p>
          No benchmark runs yet. Run the benchmark command documented in README.
        </p>
      )}
      {runs.data?.map((r) => (
        <article key={r.id}>
          <h2>{r.dataset}</h2>
          <time>{r.at}</time>
          <pre>{JSON.stringify(r.result_json, null, 2)}</pre>
        </article>
      ))}
    </main>
  );
}
