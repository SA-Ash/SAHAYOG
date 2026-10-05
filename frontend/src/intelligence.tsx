import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import { api, ApiError } from "./api";

cytoscape.use(dagre);
type User = { role: string };
type Evidence = Record<string, unknown>;
type Node = {
  data: {
    id: string;
    chain: string;
    address: string;
    label: string;
    hop: number;
    role: string;
    cluster_id?: string;
    evidence: Evidence[];
  };
};
type Edge = {
  data: {
    id: string;
    source: string;
    target: string;
    amount: string;
    token: string;
    decimals: number;
    time: string;
    inferred: boolean;
    evidence: Evidence[];
  };
};
export type Graph = {
  trace_run_id: string;
  status: string;
  stop_summary: Record<string, number>;
  nodes: Node[];
  edges: Edge[];
};
type Scenario = {
  id: string;
  name: string;
  seed: number;
  params: Record<string, unknown>;
  source: string;
};
type Entity = { id: string; name: string; source: string };
type Attribution = {
  entity: string | null;
  confidence: number;
  status: string;
  hops: number;
  source: string;
  evidence: Evidence[];
  candidates: { entity: string; confidence: number; evidence: Evidence[] }[];
};
type Federation = {
  id: string;
  job_id: string;
  status: string;
  mode: string;
  progress: number;
  replies: { vasp: string; status: string; response_s: number | null }[];
  source: string;
};
type Job = {
  id: string;
  status: string;
  progress: number;
  error?: { message: string };
  result: unknown;
};
type ProbeCluster = {
  id: string;
  entity: string;
  chain: string;
  hub_address: string;
  matching_sweeps: number;
  source: string;
  last_confirmed: string;
  freshness: number;
  stale: boolean;
};
type Vasp = {
  id: string;
  name: string;
  country: string;
  registered_fiu: boolean;
  channel: string;
  avg_response_s: number;
  response_rate: number;
  responsiveness_ewma: number;
  source: string;
  last_updated: string;
  attempts: number;
};
const chains = ["tron", "ethereum", "bnb", "polygon", "bitcoin"];
function ErrorMessage({
  error,
  missing = false,
}: {
  error: unknown;
  missing?: boolean;
}) {
  if (!error || (missing && error instanceof ApiError && error.status === 404))
    return null;
  return (
    <p role="alert" className="error">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}
function Source({ source }: { source: string }) {
  return (
    <span className="badge">
      {["simulated", "synthetic", "demo_seed"].includes(source)
        ? "Simulated · "
        : ""}
      {source}
    </span>
  );
}
function EvidenceList({ items }: { items: Evidence[] }) {
  return (
    <ul className="evidence">
      {items.map((e, i) => (
        <li key={i}>
          <strong>
            {String(e.signal || e.rule || "evidence").replaceAll("_", " ")}
          </strong>
          {e.weight !== undefined && (
            <span> · weight {Math.round(Number(e.weight) * 100)}%</span>
          )}
          <details>
            <summary>Explain</summary>
            <pre>{JSON.stringify(e, null, 2)}</pre>
          </details>
        </li>
      ))}
    </ul>
  );
}
function ChainSelect({
  value,
  setValue,
}: {
  value: string;
  setValue: (v: string) => void;
}) {
  return (
    <label>
      Chain
      <select
        aria-label="Chain"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      >
        {chains.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
    </label>
  );
}
export function CaseGraph({
  graph,
  onSelect,
}: {
  graph: Graph;
  onSelect: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState("");
  useEffect(() => {
    if (!ref.current) return;
    const cy = cytoscape({
      container: ref.current,
      elements: [...graph.nodes, ...graph.edges],
      layout: { name: "dagre", rankDir: "LR" } as cytoscape.LayoutOptions,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "#6b8396",
            color: "#263e50",
            "font-size": 10,
            "text-valign": "bottom",
            "text-margin-y": 7,
            width: 30,
            height: 30,
          },
        },
        {
          selector: 'node[role="fence"]',
          style: {
            "background-opacity": 0.05,
            "border-width": 3,
            "border-style": "dashed",
            "border-color": "#00948c",
            padding: "20px",
            "text-valign": "top",
          },
        },
        {
          selector: 'node[role="victim"]',
          style: { "background-color": "#9a6abd" },
        },
        {
          selector: 'node[role="deposit_candidate"]',
          style: { "background-color": "#e5ac43" },
        },
        {
          selector:
            'node[role="hub_candidate"],node[role="hot_wallet"],node[role="exchange"],node[role="exchange_cluster"]',
          style: { "background-color": "#00948c", width: 42, height: 42 },
        },
        {
          selector: 'node[role="bridge"],node[role="swap_service"]',
          style: { "background-color": "#477fbe" },
        },
        {
          selector: 'node[role="mixer"]',
          style: { "background-color": "#c86b69" },
        },
        {
          selector: "edge",
          style: {
            width: 2,
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "line-color": "#a3b7c5",
            "target-arrow-color": "#a3b7c5",
          },
        },
        {
          selector: "edge[inferred = true]",
          style: { "line-style": "dashed", "line-color": "#ca9940" },
        },
        {
          selector: ":selected",
          style: { "border-color": "#142b3c", "border-width": 3 },
        },
        {
          selector: "edge[colour]",
          style: {
            "line-color": "data(colour)",
            "target-arrow-color": "data(colour)",
            width: "data(flow_width)",
          },
        },
        {
          selector: 'node[role="forecast"]',
          style: {
            opacity: 0.45,
            "background-color": "#709cbd",
            "text-wrap": "wrap",
            "text-max-width": "120px",
          },
        },
        {
          selector: 'edge[token="forecast"]',
          style: { opacity: 0.35, "line-style": "dashed" },
        },
      ],
    });
    cy.on("tap", "node", (event) => onSelect(event.target.id()));
    cy.on("mouseover", "edge", (event) => {
      const data = event.target.data();
      setHover(
        `${data.amount} base units ${data.token} (${data.decimals} decimals) · ${data.time}`,
      );
    });
    cy.on("mouseout", "edge", () => setHover(""));
    return () => cy.destroy();
  }, [graph, onSelect]);
  return (
    <>
      <div ref={ref} className="graph" aria-label="Transaction trace graph" />
      {hover && <p role="tooltip">{hover}</p>}
      <p className="muted">
        Select a node to inspect evidence. Purple: victim · Gold: deposit ·
        Teal: hub/exchange · Red: likely mixer · Blue: bridge/swap. Dashed edges
        are inferred.
      </p>
    </>
  );
}
export function CaseInvestigation({
  caseId,
  scenarioId,
  user,
  activePanel,
}: {
  caseId: string;
  scenarioId?: string;
  user: User;
  activePanel?: string;
}) {
  const client = useQueryClient();
  const [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(""),
    [jobId, setJobId] = useState(""),
    [selected, setSelected] = useState(""),
    [maxHops, setMaxHops] = useState(12),
    [minAmount, setMinAmount] = useState("1"),
    [fanout, setFanout] = useState(20),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [privacy, setPrivacy] = useState("hash"),
    [routing, setRouting] = useState<{
      channel: string;
      target_vasp: string;
      issuer_target: string;
      source: string;
      evidence: Evidence[];
      ranking: { vasp: string; responsiveness: number }[];
    }>();
  const graph = useQuery({
    queryKey: ["graph", caseId],
    queryFn: () => api<Graph>("/cases/" + caseId + "/graph"),
    retry: false,
    refetchInterval: (q) =>
      ["RUNNING", "QUEUED"].includes(q.state.data?.status || "") ? 700 : false,
  });
  const attribution = useQuery({
    queryKey: ["attribution", caseId],
    queryFn: () => api<Attribution>("/cases/" + caseId + "/attribution"),
    retry: false,
    enabled: graph.data?.status === "COMPLETED",
  });
  const federation = useQuery({
    queryKey: ["federation", caseId],
    queryFn: () => api<Federation>("/cases/" + caseId + "/federated-lookup"),
    retry: false,
    refetchInterval: (q) =>
      q.state.data && ["QUEUED", "RUNNING"].includes(q.state.data.status)
        ? 800
        : false,
  });
  const patterns = useQuery({
    queryKey: ["patterns", caseId],
    queryFn: () =>
      api<{ id: string; kind: string; score: number; evidence: Evidence[] }[]>(
        "/cases/" + caseId + "/patterns",
      ),
    retry: false,
    enabled: graph.data?.status === "COMPLETED",
  });
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>("/jobs/" + jobId),
    enabled: !!jobId,
    refetchInterval: (q) =>
      q.state.data && ["COMPLETED", "FAILED"].includes(q.state.data.status)
        ? false
        : 600,
  });
  const why = useQuery({
    queryKey: ["why", selected],
    queryFn: () =>
      api<{
        role: string;
        address: string;
        chain: string;
        hop: number;
        evidence: Evidence[];
        profile: Record<string, unknown>;
        labels: { entity: string; source: string; last_confirmed_at: string }[];
        cluster_id?: string;
      }>("/nodes/" + selected + "/why"),
    enabled: !!selected,
  });
  const cluster = useQuery({
    queryKey: ["cluster", why.data?.cluster_id],
    queryFn: () =>
      api<{ members: { address: string; evidence: Evidence[] }[] }>(
        "/clusters/" + why.data!.cluster_id,
      ),
    enabled: !!why.data?.cluster_id,
  });
  useEffect(() => {
    let socket: WebSocket;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    const refresh = () => {
      for (const key of [
        "graph",
        "attribution",
        "patterns",
        "federation",
        "case",
        "workspace",
      ])
        client.invalidateQueries({ queryKey: [key, caseId] });
    };
    const connect = () => {
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws/cases/${caseId}?after=${cursor}`,
      );
      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.id) cursor = data.id;
        if (data.type !== "heartbeat") refresh();
      };
      socket.onclose = (event) => {
        if (!stopped && event.code !== 4401 && event.code !== 4403)
          timer = setTimeout(connect, 1500);
      };
    };
    const initialConnect = window.setTimeout(connect, 0);
    return () => {
      window.clearTimeout(initialConnect);
      stopped = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [caseId, client]);
  useEffect(() => {
    if (job.data && ["COMPLETED", "FAILED"].includes(job.data.status))
      for (const key of [
        "graph",
        "attribution",
        "patterns",
        "federation",
        "case",
        "workspace",
      ])
        client.invalidateQueries({ queryKey: [key, caseId] });
  }, [job.data?.status, caseId, client]);
  async function action(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy("");
    }
  }
  const running =
    graph.data && ["RUNNING", "QUEUED"].includes(graph.data.status);
  return (
    <section className="panel investigation">
      <div className="heading">
        <h2>Investigation</h2>
        {scenarioId && <Source source="simulated" />}
      </div>
      {user.role === "INVESTIGATOR" &&
        (!activePanel || activePanel === "Trace") && (
          <form
            className="trace-controls"
            onSubmit={(e) => {
              e.preventDefault();
              action("trace", async () => {
                const result = await api<{ job_id: string }>(
                  "/cases/" + caseId + "/trace",
                  {
                    max_hops: maxHops,
                    min_amount: minAmount,
                    max_fanout: fanout,
                    time_from: from || null,
                    time_to: to || null,
                  },
                );
                setJobId(result.job_id);
                await client.invalidateQueries({ queryKey: ["graph", caseId] });
              });
            }}
          >
            <label>
              Max hops
              <input
                type="number"
                min={1}
                max={30}
                value={maxHops}
                onChange={(e) => setMaxHops(Number(e.target.value))}
              />
            </label>
            <label>
              Min amount (base units)
              <input
                pattern="[0-9]{1,38}"
                value={minAmount}
                onChange={(e) => setMinAmount(e.target.value)}
                required
              />
            </label>
            <label>
              Max fan-out
              <input
                type="number"
                min={1}
                max={100}
                value={fanout}
                onChange={(e) => setFanout(Number(e.target.value))}
              />
            </label>
            <label>
              From (ISO time, optional)
              <input
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                placeholder="2026-10-01T00:00:00Z"
              />
            </label>
            <label>
              To (ISO time, optional)
              <input value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
            <button disabled={!!busy || !!running}>
              {running ? "Tracing…" : "Run trace"}
            </button>
          </form>
        )}
      {job.data && (
        <div role="status">
          <p>
            {job.data.status} · {job.data.progress}%
          </p>
          <progress value={job.data.progress} max={100} />
          {job.data.error && (
            <p role="alert" className="error">
              {job.data.error.message}
            </p>
          )}
        </div>
      )}
      <ErrorMessage error={error} />
      <ErrorMessage error={graph.error} missing />
      {!graph.data && (
        <p className="muted">
          Run a trace to follow outgoing funds and examine possible exchange
          hubs.
        </p>
      )}
      {graph.data && (
        <>
          <div className="graph-grid">
            <div>
              <CaseGraph graph={graph.data} onSelect={setSelected} />
              <div className="inline">
                {Object.entries(graph.data.stop_summary).map(
                  ([reason, count]) => (
                    <span className="badge" key={reason}>
                      {reason.replaceAll("_", " ")}: {count}
                    </span>
                  ),
                )}
              </div>
              <p>
                {graph.data.nodes.length} nodes · {graph.data.edges.length}{" "}
                transfers · {graph.data.status}
              </p>
            </div>
            <div className="inspector">
              <h3>Node inspector</h3>
              {!selected ? (
                <p>Select a node in the graph.</p>
              ) : (
                <>
                  <ErrorMessage error={why.error} />
                  {why.data && (
                    <>
                      <strong>
                        {why.data.chain} · {why.data.role}
                      </strong>
                      <p>
                        <code>{why.data.address}</code>
                      </p>
                      <EvidenceList items={why.data.evidence} />
                      {why.data.labels.map((label, i) => (
                        <p key={i}>
                          {label.entity} <Source source={label.source} />
                          <br />
                          Confirmed{" "}
                          {new Date(
                            label.last_confirmed_at,
                          ).toLocaleDateString()}
                        </p>
                      ))}
                      <details>
                        <summary>Address profile</summary>
                        <pre>{JSON.stringify(why.data.profile, null, 2)}</pre>
                      </details>
                      {cluster.data && (
                        <details>
                          <summary>
                            Cluster · {cluster.data.members.length} members
                          </summary>
                          {cluster.data.members.map((m) => (
                            <p key={m.address}>
                              <code>{m.address}</code>
                            </p>
                          ))}
                        </details>
                      )}
                    </>
                  )}
                </>
              )}
            </div>
          </div>
          {patterns.data && patterns.data.length > 0 && (
            <section>
              <h3>Patterns</h3>
              {patterns.data.map((p) => (
                <details key={p.id}>
                  <summary>
                    {p.kind} · {Math.round(p.score * 100)}% heuristic score
                  </summary>
                  <EvidenceList items={p.evidence} />
                </details>
              ))}
            </section>
          )}
          <details>
            <summary>Transfers and amounts</summary>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Base units</th>
                    <th>Decimals</th>
                    <th>Time</th>
                    <th>Link</th>
                  </tr>
                </thead>
                <tbody>
                  {graph.data.edges.map((e) => (
                    <tr key={e.data.id}>
                      <td>{e.data.token}</td>
                      <td>
                        <code>{e.data.amount}</code>
                      </td>
                      <td>{e.data.decimals}</td>
                      <td>{new Date(e.data.time).toLocaleString()}</td>
                      <td>{e.data.inferred ? "Probabilistic" : "Observed"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <div hidden={!!activePanel && activePanel !== "Attribution"}>
            <div className="heading">
              <h3>Attribution</h3>
              {user.role === "INVESTIGATOR" && (
                <button
                  disabled={!!busy || graph.data.status !== "COMPLETED"}
                  onClick={() =>
                    action("attribute", async () => {
                      await api("/cases/" + caseId + "/attribute", {});
                      await client.invalidateQueries({
                        queryKey: ["attribution", caseId],
                      });
                      await client.invalidateQueries({
                        queryKey: ["federation", caseId],
                      });
                    })
                  }
                >
                  Run attribution
                </button>
              )}
            </div>
            <ErrorMessage error={attribution.error} missing />
            {attribution.data && (
              <section className="result">
                <h3>{attribution.data.entity || "No entity identified"}</h3>
                <div className="confidence">
                  <strong>
                    {Math.round(attribution.data.confidence * 100)}%
                  </strong>
                  <span>
                    {attribution.data.status} · {attribution.data.hops ?? "—"}{" "}
                    hops
                  </span>
                </div>
                <Source source={attribution.data.source} />
                <EvidenceList items={attribution.data.evidence} />
                {attribution.data.candidates.length > 1 && (
                  <details>
                    <summary>Other candidates</summary>
                    {attribution.data.candidates.map((c) => (
                      <p key={c.entity}>
                        {c.entity}: {Math.round(c.confidence * 100)}%
                      </p>
                    ))}
                  </details>
                )}
              </section>
            )}
            <div className="heading">
              <h3>Federated VASP lookup</h3>
              {user.role === "INVESTIGATOR" && (
                <div className="inline">
                  <label>
                    Privacy mode
                    <select
                      value={privacy}
                      onChange={(e) => setPrivacy(e.target.value)}
                    >
                      <option value="hash">Salted hash</option>
                      <option value="psi">PSI</option>
                    </select>
                  </label>
                  <button
                    disabled={
                      !!busy ||
                      graph.data.status !== "COMPLETED" ||
                      federation.data?.status === "RUNNING"
                    }
                    onClick={() =>
                      action("lookup", async () => {
                        const r = await api<{ job_id: string }>(
                          "/cases/" + caseId + "/federated-lookup",
                          { mode: privacy },
                        );
                        setJobId(r.job_id);
                        await client.invalidateQueries({
                          queryKey: ["federation", caseId],
                        });
                      })
                    }
                  >
                    Ask VASPs
                  </button>
                </div>
              )}
            </div>
            <p className="muted">
              Salted hashes conceal direct address text but can be tested
              against guesses. PSI compares blinded sets. Both modes query the
              configured VASPs.
            </p>
            <ErrorMessage error={federation.error} missing />
            {federation.data && (
              <>
                <Source source={federation.data.source} />
                <p>
                  {federation.data.status} · {federation.data.mode}
                </p>
                <div className="reply-grid">
                  {federation.data.replies.map((r) => (
                    <div key={r.vasp} className={"reply " + r.status}>
                      <strong>{r.vasp}</strong>
                      <span>{r.status}</span>
                      <small>
                        {r.response_s !== null
                          ? r.response_s.toFixed(2) + "s"
                          : "Awaiting reply"}
                      </small>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
          <div hidden={!!activePanel && activePanel !== "Routing"}>
            <div className="heading">
              <h3>Routing recommendation</h3>
              {user.role === "INVESTIGATOR" && (
                <button
                  disabled={!!busy}
                  onClick={() =>
                    action("route", async () =>
                      setRouting(await api("/cases/" + caseId + "/route", {})),
                    )
                  }
                >
                  Recommend channel
                </button>
              )}
            </div>
            {routing && (
              <div className="result">
                <strong>{routing.channel.replaceAll("_", " ")}</strong>
                <p>
                  {routing.target_vasp || "Target needs review"}
                  {routing.issuer_target &&
                    " · issuer: " + routing.issuer_target}
                </p>
                <Source source={routing.source} />
                <EvidenceList items={routing.evidence} />
                <p className="muted">
                  Recommendation prepared for officer review.
                </p>
                <details>
                  <summary>VASP responsiveness ranking</summary>
                  {routing.ranking.map((r) => (
                    <p key={r.vasp}>
                      {r.vasp} · {Math.round(r.responsiveness * 100)}%
                    </p>
                  ))}
                </details>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
export function ScenarioPage({ user }: { user: User }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [preset, setPreset] = useState("hard"),
    [seed, setSeed] = useState(42),
    [chain, setChain] = useState("tron"),
    [noise, setNoise] = useState(20),
    [hops, setHops] = useState(4),
    [split, setSplit] = useState(0),
    [peel, setPeel] = useState(0),
    [farm, setFarm] = useState(0),
    [dormant, setDormant] = useState(0),
    [bridge, setBridge] = useState(false),
    [swap, setSwap] = useState(false),
    [mixer, setMixer] = useState(false),
    [victims, setVictims] = useState(1),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState("");
  const [calibration, setCalibration] = useState<Record<string, unknown>>();
  const scenarios = useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api<Scenario[]>("/dev/scenarios"),
  });
  const truth = useQuery({
    queryKey: ["truth", selected],
    queryFn: () =>
      api<Record<string, unknown>>("/dev/scenarios/" + selected + "/truth"),
    enabled: !!selected,
  });
  return (
    <>
      <h1>Synthetic scenarios</h1>
      <p>Reproducible complaint paths with known ground truth.</p>
      <Source source="simulated" />
      {["INVESTIGATOR", "ADMIN"].includes(user.role) && (
        <form
          className="panel"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError(undefined);
            try {
              const s = await api<Scenario>("/dev/scenarios", {
                preset,
                seed,
                chain,
                victims,
                hops,
                noise_level: noise,
                split_merge_intensity: split,
                peel_chain_len: peel,
                farm_size: farm,
                dormant_days: dormant,
                with_bridge: bridge,
                with_swap: swap,
                mixer,
              });
              setSelected(s.id);
              await client.invalidateQueries({ queryKey: ["scenarios"] });
            } catch (e) {
              setError(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="inline">
            {["easy", "hard", "multi-victim"].map((p) => (
              <button
                type="button"
                className={p === preset ? "" : "secondary"}
                onClick={() => setPreset(p)}
                key={p}
              >
                {p}
              </button>
            ))}
          </div>
          <div className="fields">
            <ChainSelect value={chain} setValue={setChain} />
            {[
              ["Seed", seed, setSeed, 0, 2147483647],
              ["Victims", victims, setVictims, 1, 20],
              ["Hops", hops, setHops, 1, 12],
              ["Noise transfers", noise, setNoise, 0, 300],
              ["Split / merge cycles", split, setSplit, 0, 5],
              ["Peel chain length", peel, setPeel, 0, 12],
              ["Wallet farm size", farm, setFarm, 0, 30],
              ["Dormant days", dormant, setDormant, 0, 365],
            ].map(([label, value, set, min, max]) => (
              <label key={String(label)}>
                {String(label)}
                <input
                  type="number"
                  value={Number(value)}
                  min={Number(min)}
                  max={Number(max)}
                  onChange={(e) =>
                    (set as (v: number) => void)(Number(e.target.value))
                  }
                />
              </label>
            ))}
          </div>
          <div className="inline">
            {[
              ["Bridge", bridge, setBridge],
              ["Swap", swap, setSwap],
              ["Mixer", mixer, setMixer],
            ].map(([label, value, set]) => (
              <label className="check" key={String(label)}>
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  onChange={(e) =>
                    (set as (v: boolean) => void)(e.target.checked)
                  }
                />
                {String(label)}
              </label>
            ))}
          </div>
          <p className="muted">
            Bridge and swap scenarios use Ethereum or Polygon. Multi-victim
            creates five complaints.
          </p>
          <button disabled={busy}>Generate scenario</button>
          <ErrorMessage error={error} />
        </form>
      )}
      {user.role === "ADMIN" && (
        <section className="panel">
          <h2>Confidence calibration</h2>
          <p>
            Fit attributed synthetic cases against ground truth. This training
            fit applies only to synthetic investigations.
          </p>
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(undefined);
              try {
                setCalibration(await api("/dev/calibration/train", {}));
              } catch (e) {
                setError(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            Train confidence map
          </button>
          {calibration && <pre>{JSON.stringify(calibration, null, 2)}</pre>}
        </section>
      )}
      <ErrorMessage error={scenarios.error} />
      <section className="panel">
        <h2>Scenario library</h2>
        {scenarios.data?.length === 0 && <p>No scenarios yet.</p>}
        {scenarios.data?.map((s) => (
          <div className="scenario-row" key={s.id}>
            <div>
              <strong>{s.name}</strong>
              <p>
                Seed {s.seed} · {String(s.params.chain)} ·{" "}
                {String(s.params.hops)} hops
              </p>
              <code>{s.id}</code>
            </div>
            <div className="inline">
              <button className="secondary" onClick={() => setSelected(s.id)}>
                Ground truth
              </button>
              {user.role === "INVESTIGATOR" && (
                <button
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      const cases = await api<{ id: string }[]>(
                        "/dev/scenarios/" + s.id + "/load-as-case",
                        {},
                      );
                      await client.invalidateQueries({ queryKey: ["cases"] });
                      navigate("/cases/" + cases[0].id);
                    } catch (e) {
                      setError(e);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Load as case
                </button>
              )}
            </div>
          </div>
        ))}
      </section>
      {truth.data && (
        <details className="panel" open>
          <summary>Ground truth · simulated</summary>
          <pre>{JSON.stringify(truth.data, null, 2)}</pre>
        </details>
      )}
    </>
  );
}
export function ChainsPage({ user }: { user: User }) {
  const client = useQueryClient();
  const [chain, setChain] = useState("tron"),
    [hash, setHash] = useState(""),
    [address, setAddress] = useState(""),
    [scenario, setScenario] = useState(""),
    [transfers, setTransfers] = useState<Record<string, unknown>[]>([]),
    [profile, setProfile] = useState<Record<string, unknown>>(),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false);
  const status = useQuery({
    queryKey: ["chains"],
    queryFn: () =>
      api<{
        replay_mode: boolean;
        chains: { chain: string; status: string; provider: string }[];
      }>("/chains"),
  });
  const scenarios = useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api<Scenario[]>("/dev/scenarios"),
    retry: false,
  });
  const suffix = scenario ? "?scenario_id=" + scenario : "";
  async function lookup(kind: string) {
    setBusy(true);
    setError(undefined);
    try {
      if (kind === "tx")
        setTransfers(
          (
            await api<{ transfers: Record<string, unknown>[] }>(
              "/chain/" + chain + "/tx/" + hash + suffix,
            )
          ).transfers,
        );
      else
        setProfile(
          await api(
            "/chain/" + chain + "/address/" + address + "/profile" + suffix,
          ),
        );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h1>Chain lookup</h1>
      <section className="panel">
        <h2>Adapter status</h2>
        <ErrorMessage error={status.error} />
        <div className="reply-grid">
          {status.data?.chains.map((c) => (
            <div className="reply" key={c.chain}>
              <strong>{c.chain}</strong>
              <span>{c.provider}</span>
              <span>{c.status}</span>
            </div>
          ))}
        </div>
        {user.role === "ADMIN" && status.data && (
          <button
            onClick={async () => {
              try {
                await api("/admin/replay-mode", {
                  enabled: !status.data.replay_mode,
                });
                await client.invalidateQueries({ queryKey: ["chains"] });
              } catch (e) {
                setError(e);
              }
            }}
          >
            {status.data.replay_mode
              ? "Enable live requests"
              : "Enable replay mode"}
          </button>
        )}
        <p className="muted">
          Replay reads recorded responses. A missing record produces a
          cache-miss message.
        </p>
      </section>
      <section className="panel">
        <div className="fields">
          <ChainSelect value={chain} setValue={setChain} />
          <label>
            Scenario (optional)
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            >
              <option value="">Provider data</option>
              {scenarios.data?.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.name} · {s.seed}
                </option>
              ))}
            </select>
          </label>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            lookup("tx");
          }}
        >
          <label>
            Transaction hash
            <input
              value={hash}
              onChange={(e) => setHash(e.target.value)}
              required
            />
          </label>
          <button disabled={busy}>Look up transaction</button>
        </form>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            lookup("profile");
          }}
        >
          <label>
            Wallet address
            <input
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              required
            />
          </label>
          <button disabled={busy}>Look up profile</button>
        </form>
        <ErrorMessage error={error} />
      </section>
      {transfers.length > 0 && (
        <section className="panel">
          <h2>Transfers</h2>
          {transfers.map((t, i) => (
            <details key={i}>
              <summary>
                {String(t.token)} · {String(t.amount)} base units · decimals{" "}
                {String(t.decimals)} <Source source={String(t.source)} />
              </summary>
              <pre>{JSON.stringify(t, null, 2)}</pre>
            </details>
          ))}
        </section>
      )}
      {profile && (
        <section className="panel">
          <h2>Address profile</h2>
          <pre>{JSON.stringify(profile, null, 2)}</pre>
        </section>
      )}
    </>
  );
}
export function ProbeMapPage({ user }: { user: User }) {
  const client = useQueryClient();
  const [selected, setSelected] = useState(""),
    [chain, setChain] = useState("tron"),
    [count, setCount] = useState(10),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false);
  const exchanges = useQuery({
    queryKey: ["probe-exchanges"],
    queryFn: () =>
      api<
        (Entity & {
          hotwallet_count: number;
          matching_sweeps: number;
          last_probe: string;
        })[]
      >("/probes/exchanges"),
  });
  const details = useQuery({
    queryKey: ["hotwallets", selected],
    queryFn: () =>
      api<{
        clusters: ProbeCluster[];
        probes: {
          id: string;
          chain: string;
          deposit_address: string;
          swept_to: string;
          amount: string;
          sweep_delay_s: number;
          source: string;
          run_at: string;
        }[];
      }>("/probes/exchanges/" + selected + "/hotwallets"),
    enabled: !!selected,
  });
  return (
    <>
      <h1>Probe map</h1>
      <p>
        Demo probes model exchange sweeps. Live probing is an integration hook.
      </p>
      <Source source="demo_seed" />
      <ErrorMessage error={exchanges.error} />
      <section className="panel">
        <label>
          Exchange
          <select
            aria-label="Exchange"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">Choose an exchange</option>
            {exchanges.data?.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} · {e.hotwallet_count} hubs · {e.matching_sweeps} sweeps
              </option>
            ))}
          </select>
        </label>
        {user.role === "ADMIN" && (
          <>
            <form
              className="inline"
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                try {
                  await api("/probes/run", {
                    entity_id: selected,
                    chain,
                    count,
                    seed: 42,
                  });
                  await client.invalidateQueries({
                    queryKey: ["hotwallets", selected],
                  });
                  await client.invalidateQueries({
                    queryKey: ["probe-exchanges"],
                  });
                  setError(undefined);
                } catch (e) {
                  setError(e);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <ChainSelect value={chain} setValue={setChain} />
              <label>
                Probe count
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                />
              </label>
              <button disabled={!selected || busy}>Run simulated probe</button>
            </form>
            <CsvUpload
              endpoint="/probes/import"
              onDone={() => {
                client.invalidateQueries({ queryKey: ["hotwallets"] });
                client.invalidateQueries({ queryKey: ["probe-exchanges"] });
              }}
            />
          </>
        )}
        <ErrorMessage error={error} />
      </section>
      <ErrorMessage error={details.error} />
      {details.data && (
        <>
          <section className="panel">
            <h2>Hot wallets</h2>
            {details.data.clusters.map((c) => (
              <article key={c.id}>
                <strong>
                  {c.chain} · {c.matching_sweeps} matching sweeps
                </strong>
                <p>
                  <code>{c.hub_address}</code>
                </p>
                <Source source={c.source} />
                <p>
                  Confirmed {new Date(c.last_confirmed).toLocaleString()} ·{" "}
                  {c.stale ? "Stale" : "Fresh"}
                </p>
                <progress value={c.freshness} max={1} />
              </article>
            ))}
          </section>
          <details className="panel">
            <summary>Probe log · latest 100</summary>
            {details.data.probes.map((p) => (
              <p key={p.id}>
                {p.chain} · {p.amount} base units · sweep in {p.sweep_delay_s}s
                · <Source source={p.source} />
                <br />
                <code>
                  {p.deposit_address} → {p.swept_to}
                </code>
              </p>
            ))}
          </details>
        </>
      )}
    </>
  );
}
export function VaspDirectoryPage() {
  const [selected, setSelected] = useState("");
  const vasps = useQuery({
    queryKey: ["vasps"],
    queryFn: () => api<Vasp[]>("/vasps"),
  });
  const stats = useQuery({
    queryKey: ["vasp-stats", selected],
    queryFn: () =>
      api<Vasp & { history: { status: string; response_s: number | null }[] }>(
        "/vasps/" + selected + "/stats",
      ),
    enabled: !!selected,
  });
  return (
    <>
      <h1>VASP directory</h1>
      <ErrorMessage error={vasps.error} />
      <section className="panel tablewrap">
        <table>
          <thead>
            <tr>
              <th>VASP</th>
              <th>Country</th>
              <th>FIU registered</th>
              <th>Channel</th>
              <th>Responsiveness</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {vasps.data?.map((v) => (
              <tr key={v.id}>
                <td>
                  <button
                    className="secondary"
                    onClick={() => setSelected(v.id)}
                  >
                    {v.name}
                  </button>
                </td>
                <td>{v.country}</td>
                <td>{v.registered_fiu ? "Yes" : "No"}</td>
                <td>{v.channel.replaceAll("_", " ")}</td>
                <td>
                  {Math.round(v.responsiveness_ewma * 100)}% · {v.attempts}{" "}
                  queries
                </td>
                <td>
                  <Source source={v.source} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {stats.data && (
        <section className="panel">
          <h2>{stats.data.name}</h2>
          <p>
            Response rate {Math.round(stats.data.response_rate * 100)}% ·
            average {stats.data.avg_response_s.toFixed(2)}s
          </p>
          <div className="sparkline" aria-label="Response history">
            {stats.data.history
              .slice()
              .reverse()
              .map((h, i) => (
                <span
                  key={i}
                  title={h.status + " " + h.response_s}
                  style={{
                    height:
                      Math.max(
                        5,
                        h.status === "timeout"
                          ? 5
                          : 100 / (1 + (h.response_s || 0)),
                      ) + "%",
                    background: h.status === "yes" ? "#00948c" : "#93aabd",
                  }}
                />
              ))}
          </div>
          <p className="muted">
            History shows response speed; silent replies remain at the baseline.
          </p>
        </section>
      )}
    </>
  );
}
function CsvUpload({
  endpoint,
  onDone,
}: {
  endpoint: string;
  onDone: () => void;
}) {
  const [error, setError] = useState<unknown>(),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <label>
      Import CSV
      <input
        type="file"
        accept=".csv,text/csv"
        disabled={busy}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          setBusy(true);
          try {
            const form = new FormData();
            form.append("file", file);
            const result = await api<{ imported: number }>(endpoint, form);
            setMessage("Imported " + result.imported + " records");
            setError(undefined);
            onDone();
          } catch (e) {
            setError(e);
          } finally {
            setBusy(false);
            e.target.value = "";
          }
        }}
      />
      <span role="status">{message}</span>
      <ErrorMessage error={error} />
    </label>
  );
}
export function LabelsPage({ user }: { user: User }) {
  const [chain, setChain] = useState("ethereum"),
    [address, setAddress] = useState(""),
    [labels, setLabels] = useState<
      {
        entity: string;
        source: string;
        data_source: string;
        confidence: number;
        last_confirmed_at: string;
        evidence: Evidence[];
      }[]
    >([]),
    [entity, setEntity] = useState(""),
    [reference, setReference] = useState(""),
    [error, setError] = useState<unknown>();
  const entities = useQuery({
    queryKey: ["entities"],
    queryFn: () => api<Entity[]>("/entities"),
  });
  async function lookup() {
    try {
      setLabels(
        await api(
          "/labels?chain=" + chain + "&address=" + encodeURIComponent(address),
        ),
      );
      setError(undefined);
    } catch (e) {
      setError(e);
    }
  }
  return (
    <>
      <h1>Labels and evidence</h1>
      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          lookup();
        }}
      >
        <ChainSelect value={chain} setValue={setChain} />
        <label>
          Wallet address
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            required
          />
        </label>
        <button>Look up label</button>
        <ErrorMessage error={error} />
      </form>
      {labels.map((l, i) => (
        <section className="panel" key={i}>
          <strong>
            {l.entity} · {Math.round(l.confidence * 100)}%
          </strong>
          <p>
            {l.source} · confirmed{" "}
            {new Date(l.last_confirmed_at).toLocaleDateString()}
          </p>
          <Source source={l.data_source} />
          <EvidenceList items={l.evidence} />
        </section>
      ))}
      {user.role === "ADMIN" && (
        <section className="panel">
          <h2>Add public label</h2>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await api("/labels", {
                  chain,
                  address,
                  entity_id: entity,
                  source: "public",
                  evidence: [{ signal: "public_reference", reference }],
                });
                await lookup();
              } catch (e) {
                setError(e);
              }
            }}
          >
            <label>
              Entity
              <select
                value={entity}
                onChange={(e) => setEntity(e.target.value)}
                required
              >
                <option value="">Choose an entity</option>
                {entities.data?.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Evidence reference
              <input
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                required
              />
            </label>
            <p>Uses the chain and wallet address entered above.</p>
            <button>Add label</button>
          </form>
          <CsvUpload endpoint="/labels/import" onDone={lookup} />
        </section>
      )}
    </>
  );
}
