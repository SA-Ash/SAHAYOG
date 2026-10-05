import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import { api } from "./api";
import { CaseInvestigation, CaseGraph, Graph } from "./intelligence";
import { useLanguage } from "./language";

import { MonitorPanel } from "./monitoring";

type User = { role: string };
type Evidence = Record<string, unknown>;
type Asset = {
  chain: string;
  address: string;
  token: string;
  token_address: string | null;
  decimals: number;
};
type Taint = {
  id: string;
  source: string;
  method: string;
  cutoff: string;
  target: Asset;
  by_case: Record<string, string>;
  ranges: Record<string, { min: string; max: string }>;
  traceable_amount: string;
  uncoloured_amount: string;
  untraceable_amount: string;
  evidence: Evidence[];
  flows: Flow[];
  related_case_ids: string[];
  possible_unreported_victims: { amount: string; verified: boolean };
  stale: boolean;
};
type Flow = {
  id: string;
  source: (string | number | null)[];
  target: (string | number | null)[];
  amount: string;
  by_case: Record<string, string>;
  uncoloured: string;
  uncertain: boolean;
  at: string;
};
type Impact = {
  id: string;
  target: Asset;
  traceable_amount: string;
  est_balance: string;
  share_pct: string;
  other_depositors: number;
  distinct_depositors: number;
  swept: boolean;
  is_hub: boolean;
  minutes_since_arrival: number;
  address_type: string;
  address_type_confidence: number;
  impact_level: string;
  recommendation: string;
  freeze_allowed: boolean;
  max_freeze_amount: string;
  evidence: Evidence[];
  disclaimer: string;
  stale: boolean;
};
type RequestRecord = {
  id: string;
  case_id: string;
  kind: string;
  state: string;
  target_address: string;
  target_chain: string;
  impact_level: string;
  amount_by_case: Record<string, string>;
  reason: string;
  golden_hour: boolean;
  second_key_due: string | null;
  expires_at: string | null;
  approver_id: string | null;
  dispatch_error?: { message: string };
  package: Record<string, unknown>;
  timeline: { from: string; to: string; note: string; at: string }[];
};
type ReportRecord = {
  id: string;
  version: number;
  bundle_hash: string;
  merkle_root: string;
  created_at: string;
  items: { id: string; kind: string }[];
};
type ForecastRecord = {
  disclaimer: string;
  source: string;
  model_version: number;
  exit_entity_probs: {
    entity_id: string;
    entity: string;
    probability: number;
    global_probability: number;
    gang_probability: number | null;
  }[];
  eta_quantiles_seconds: {
    p10: number | null;
    p50: number | null;
    p90: number | null;
  };
  gang_blend_weight: number;
  unresolved_probability: number;
  frontiers: {
    node_id: string;
    role: string;
    chain: string;
    address: string;
    next_hops: Record<string, number>;
  }[];
  evaluation: Record<string, unknown>;
  evidence: Evidence[];
};
type Workspace = {
  case: { id: string; gang_case_id: string | null; source: string };
  graph: Graph | null;
  taint: Taint | null;
  impact: Impact | null;
  requests: RequestRecord[];
  forecast: ForecastRecord | null;
  reports: ReportRecord[];
};
const colours = [
  "#00948c",
  "#8064b6",
  "#e5ac43",
  "#477fbe",
  "#b25965",
  "#709042",
];
function ErrorMessage({ error }: { error: unknown }) {
  return error ? (
    <p role="alert" className="error">
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;
}
function Explain({ items }: { items: Evidence[] }) {
  const { t } = useLanguage();
  return (
    <details className="explain">
      <summary>{t("Explain this")}</summary>
      {items.map((e, i) => (
        <div key={i}>
          <p>{String(e.note || e.signal || e.rule || "Evidence")}</p>
          <pre>{JSON.stringify(e, null, 2)}</pre>
        </div>
      ))}
    </details>
  );
}
function Amount({ value, asset }: { value: string; asset: Asset }) {
  const amount = BigInt(value || "0"),
    base = 10n ** BigInt(asset.decimals),
    fraction = (amount % base)
      .toString()
      .padStart(asset.decimals, "0")
      .replace(/0+$/, "");
  return (
    <span title={`${value} exact base units; ${asset.decimals} decimals`}>
      {(amount / base).toString()}
      {fraction ? "." + fraction : ""} {asset.token}
    </span>
  );
}
function Pie({ taint }: { taint: Taint }) {
  const values = [
    ...Object.entries(taint.by_case),
    ["Not traceable / uncoloured", BigInt(taint.uncoloured_amount).toString()],
  ];
  const total = values.reduce((sum, [, value]) => sum + BigInt(value), 0n);
  let cursor = 0;
  const stops = values.map(([, value], i) => {
    const start = cursor;
    cursor += total ? Number((BigInt(value) * 10000n) / total) / 100 : 0;
    return `${i < values.length - 1 ? colours[i % colours.length] : "#a3b0bc"} ${start}% ${cursor}%`;
  });
  return (
    <div className="taint-pie">
      <div
        role="img"
        aria-label="Per-case traceable and uncoloured shares"
        style={{ background: `conic-gradient(${stops.join(",")})` }}
      />
      <ul>
        {values.map(([key, value], i) => (
          <li key={key}>
            <span
              className="colour-dot"
              style={{
                background:
                  i < values.length - 1
                    ? colours[i % colours.length]
                    : "#a3b0bc",
              }}
            />
            {key.length > 30 ? key.slice(0, 8) : key}:{" "}
            <Amount value={value} asset={taint.target} />
          </li>
        ))}
      </ul>
    </div>
  );
}
function OverlayGraph({
  graph,
  taint,
  forecast,
  time,
}: {
  graph: Graph;
  taint: Taint | null;
  forecast: ForecastRecord | null;
  time: number;
}) {
  const [selected, setSelected] = useState("");
  const filtered = useMemo(() => {
    const edges = graph.edges
      .filter((e) => new Date(e.data.time).getTime() <= time)
      .flatMap((edge) => {
        const source = graph.nodes.find(
          (n) => n.data.id === edge.data.source,
        )?.data;
        const target = graph.nodes.find(
          (n) => n.data.id === edge.data.target,
        )?.data;
        const flow = taint?.flows.find(
          (f) =>
            f.source[0] === source?.chain &&
            f.source[1] === source?.address &&
            f.target[0] === target?.chain &&
            f.target[1] === target?.address &&
            f.at === edge.data.time,
        );
        const swept =
          source?.address === taint?.target.address ||
          ["hub_candidate", "hot_wallet", "exchange"].includes(
            source?.role || "",
          );
        const coloured =
          flow && !swept
            ? Object.entries(flow.by_case).filter(
                ([, amount]) => BigInt(amount) > 0n,
              )
            : [];
        if (!coloured.length)
          return [{ data: { ...edge.data, colour: "#b5bcc3", flow_width: 2 } }];
        return coloured.map(([caseId, amount]) => ({
          data: {
            ...edge.data,
            id: edge.data.id + ":" + caseId,
            amount,
            colour:
              colours[
                Math.max(0, Object.keys(taint?.by_case || {}).indexOf(caseId)) %
                  colours.length
              ],
            flow_width:
              2 +
              Number((BigInt(amount) * 400n) / (BigInt(flow!.amount) || 1n)) /
                100,
            uncertain: flow?.uncertain,
          },
        }));
      });
    const visibleIds = new Set(
      edges.flatMap((e) => [e.data.source, e.data.target]),
    );
    const nodes = graph.nodes.filter(
      (n) => visibleIds.has(n.data.id) || n.data.role === "victim",
    );
    const overlay: Graph = { ...graph, nodes, edges };
    if (forecast)
      for (const frontier of forecast.frontiers) {
        if (!nodes.some((n) => n.data.id === frontier.node_id)) continue;
        for (const [role, probability] of Object.entries(frontier.next_hops)) {
          const id = "forecast:" + frontier.node_id + ":" + role;
          overlay.nodes.push({
            data: {
              id,
              chain: frontier.chain,
              address: "Forecast only",
              label: `${forecast.exit_entity_probs.find((exit) => role === "exit:" + exit.entity_id)?.entity || role} ${(probability * 100).toFixed(0)}%`,
              hop: 99,
              role: "forecast",
              evidence: [],
            },
          });
          overlay.edges.push({
            data: {
              id: "edge:" + id,
              source: frontier.node_id,
              target: id,
              amount: "0",
              token: "forecast",
              decimals: 0,
              time: new Date(time).toISOString(),
              inferred: true,
              evidence: [{ signal: "simulated_forecast", probability }],
            },
          });
        }
      }
    return overlay;
  }, [graph, taint, forecast, time]);
  return (
    <>
      <CaseGraph graph={filtered} onSelect={setSelected} />
      {selected && (
        <section className="node-inspector">
          <h4>Selected node</h4>
          <p>
            {filtered.nodes.find((n) => n.data.id === selected)?.data.address}
          </p>
          <p>
            Role:{" "}
            {filtered.nodes.find((n) => n.data.id === selected)?.data.role}
          </p>
          <Explain
            items={
              filtered.nodes.find((n) => n.data.id === selected)?.data
                .evidence || []
            }
          />
        </section>
      )}
    </>
  );
}
export function CaseWorkspace({
  caseId,
  scenarioId,
  user,
}: {
  caseId: string;
  scenarioId?: string;
  user: User;
}) {
  const { t } = useLanguage();
  const client = useQueryClient();
  const [step, setStep] = useState("Trace"),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false),
    [method, setMethod] = useState("haircut"),
    [cutoff, setCutoff] = useState("0.02"),
    [amount, setAmount] = useState(""),
    [kind, setKind] = useState("DISCLOSURE"),
    [time, setTime] = useState<number | null>(null),
    [colour, setColour] = useState(""),
    [training, setTraining] = useState<Record<string, unknown>>();
  const workspace = useQuery({
    queryKey: ["workspace", caseId],
    queryFn: () => api<Workspace>(`/cases/${caseId}/workspace`),
    refetchInterval: 1500,
  });
  const farms = useQuery({
    queryKey: ["farms", caseId],
    queryFn: () =>
      api<
        {
          id: string;
          funder: string;
          members: number;
          status: string;
          evidence: { members: string[]; chain: string };
        }[]
      >(`/cases/${caseId}/wallet-farms`),
    enabled: step === "Taint" && !!workspace.data?.graph,
  });
  const similar = useQuery({
    queryKey: ["similar", caseId],
    queryFn: () =>
      api<
        {
          case_id: string;
          case_ref: string;
          cosine: number;
          suggested: boolean;
          features: unknown;
        }[]
      >(`/cases/${caseId}/similar-operators`),
    enabled: step === "Taint" && !!workspace.data?.graph,
  });
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      await client.invalidateQueries({ queryKey: ["workspace", caseId] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const data = workspace.data,
    taint = data?.taint,
    impact = data?.impact;
  const graph = data?.graph;
  const times = graph?.edges.map((e) => new Date(e.data.time).getTime()) || [];
  const first = Math.min(...times),
    last = Math.max(...times);
  const overlayTaint =
    taint && colour
      ? {
          ...taint,
          flows: taint.flows.map((f) => ({
            ...f,
            by_case: f.by_case[colour] ? { [colour]: f.by_case[colour] } : {},
          })),
        }
      : taint || null;
  return (
    <section className="case-workspace">
      <h2>{t("Case workspace")}</h2>
      <nav className="case-stepper" aria-label="Investigation steps">
        {[
          "Trace",
          "Attribution",
          "Taint",
          "Impact",
          "Approval",
          "Routing",
          "Report",
          "Forecast",
          "Fence",
          "Dormancy",
        ].map((label, index) => (
          <button
            className={step === label ? "active" : "secondary"}
            aria-current={step === label ? "step" : undefined}
            key={label}
            onClick={() => setStep(label)}
          >
            {index + 1}. {t(label)}
          </button>
        ))}
      </nav>
      <ErrorMessage error={error || workspace.error} />
      {workspace.isPending && (
        <div className="skeleton" role="status">
          Loading investigation evidence…
        </div>
      )}
      <div
        style={{
          display: ["Trace", "Attribution", "Routing"].includes(step)
            ? "block"
            : "none",
        }}
      >
        <CaseInvestigation
          caseId={caseId}
          scenarioId={scenarioId}
          user={user}
          activePanel={step}
        />
      </div>
      {!["Trace", "Attribution", "Routing"].includes(step) && graph && (
        <section className="panel">
          <div className="heading">
            <h3>
              Observed graph {step === "Forecast" && "with forecast cone"}
            </h3>
            <span className="badge">{data?.case.source}</span>
          </div>
          <label>
            {t("Time replay")}
            <input
              aria-label="Time replay"
              type="range"
              min={first}
              max={last}
              value={time ?? last}
              onChange={(e) => setTime(Number(e.target.value))}
            />
          </label>
          <p>{new Date(time ?? last).toLocaleString()}</p>
          {taint && (
            <label>
              Case colour
              <select
                aria-label="Case colour"
                value={colour}
                onChange={(e) => setColour(e.target.value)}
              >
                <option value="">All cases</option>
                {Object.keys(taint.by_case).map((key, i) => (
                  <option value={key} key={key}>
                    Victim {i + 1} · {key.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <OverlayGraph
            graph={graph}
            taint={overlayTaint}
            forecast={step === "Forecast" ? data?.forecast || null : null}
            time={time ?? last}
          />
        </section>
      )}
      {(step === "Fence" || step === "Dormancy") && (
        <MonitorPanel
          caseId={caseId}
          user={user}
          graph={data?.graph || null}
          dormant={step === "Dormancy"}
          synthetic={!!scenarioId}
        />
      )}
      {step === "Taint" && (
        <section className="panel">
          <h3>Dye Pack</h3>
          {user.role === "INVESTIGATOR" && (
            <form
              className="inline"
              onSubmit={(e) => {
                e.preventDefault();
                run(() => api(`/cases/${caseId}/taint`, { method, cutoff }));
              }}
            >
              <label>
                Method
                <select
                  aria-label="Taint method"
                  value={method}
                  onChange={(e) => setMethod(e.target.value)}
                >
                  {["haircut", "fifo", "poison"].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label>
                Dilution cutoff
                <input
                  aria-label="Dilution cutoff"
                  type="number"
                  step="0.001"
                  min="0"
                  max="1"
                  value={cutoff}
                  onChange={(e) => setCutoff(e.target.value)}
                />
              </label>
              <button disabled={busy}>Run Dye Pack</button>
            </form>
          )}
          {!taint ? (
            <p>
              Run Dye Pack to compute a per-victim ledger and method ranges.
            </p>
          ) : (
            <>
              <p className="badge">
                {taint.source} · {taint.method}
                {taint.stale && " · stale: run again"}
              </p>
              <h4>
                Target: {taint.target.chain} · {taint.target.address}
              </h4>
              <p>
                Traceable:{" "}
                <Amount value={taint.traceable_amount} asset={taint.target} /> ·
                Uncoloured:{" "}
                <Amount value={taint.uncoloured_amount} asset={taint.target} />{" "}
                · Uncertain:{" "}
                <Amount value={taint.untraceable_amount} asset={taint.target} />
              </p>
              <Pie taint={taint} />
              <table>
                <thead>
                  <tr>
                    <th>Case colour</th>
                    <th>Traceable</th>
                    <th>FIFO–Haircut range</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(taint.by_case).map(([key, value], i) => (
                    <tr key={key}>
                      <td>
                        <span
                          className="colour-dot"
                          style={{ background: colours[i % colours.length] }}
                        />
                        {key.slice(0, 8)}
                      </td>
                      <td>
                        <Amount value={value} asset={taint.target} />
                      </td>
                      <td>
                        <Amount
                          value={taint.ranges[key].min}
                          asset={taint.target}
                        />{" "}
                        –{" "}
                        <Amount
                          value={taint.ranges[key].max}
                          asset={taint.target}
                        />
                        <progress
                          max={Number(BigInt(taint.ranges[key].max) || 1n)}
                          value={Number(BigInt(taint.ranges[key].min))}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>
                Possible unreported-victim estimate:{" "}
                <Amount
                  value={taint.possible_unreported_victims.amount}
                  asset={taint.target}
                />{" "}
                · unverified
              </p>
              <Explain items={taint.evidence} />
            </>
          )}
          {data?.case.gang_case_id && (
            <p>
              <Link to={`/gang/${data.case.gang_case_id}`}>
                Open linked gang case and restitution preview
              </Link>
            </p>
          )}
          <h4>Wallet farms</h4>
          {farms.data?.length === 0 && <p>No qualifying farm found.</p>}
          {farms.data?.map((farm) => (
            <details key={farm.id}>
              <summary>
                {farm.members} wallets · {farm.status}
              </summary>
              <code>{farm.funder}</code>
              {farm.evidence.members.map((address) => (
                <p key={address}>{address}</p>
              ))}
              {user.role === "INVESTIGATOR" && farm.status === "suggested" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api(`/wallet-farms/${farm.id}/approve`, {});
                      client.invalidateQueries({ queryKey: ["farms", caseId] });
                    })
                  }
                >
                  Approve farm link
                </button>
              )}
            </details>
          ))}
          <h4>Similar operators · suggestions only</h4>
          {similar.data?.map((r) => (
            <details key={r.case_id}>
              <summary>
                {r.case_ref} · {(r.cosine * 100).toFixed(0)}% similarity
              </summary>
              <pre>{JSON.stringify(r.features, null, 2)}</pre>
            </details>
          ))}
        </section>
      )}
      {step === "Impact" && (
        <section className="panel">
          <h3>Blast Radius</h3>
          {user.role === "INVESTIGATOR" && (
            <button
              disabled={busy || !taint || taint.stale}
              onClick={() =>
                run(() => api(`/cases/${caseId}/blast-radius`, {}))
              }
            >
              Compute impact
            </button>
          )}
          {!impact ? (
            <p>
              Run Dye Pack, then compute the impact of the deposit/account
              target.
            </p>
          ) : (
            <>
              <strong className={`impact ${impact.impact_level.toLowerCase()}`}>
                {impact.impact_level}
              </strong>
              <p>{impact.recommendation.replaceAll("_", " ")}</p>
              <div className="metric-grid">
                <span>
                  Traceable share: {Number(impact.share_pct).toFixed(1)}%
                </span>
                <span>Other depositors: {impact.other_depositors}</span>
                <span>Swept: {impact.swept ? "Yes" : "No"}</span>
                <span>Hub: {impact.is_hub ? "Yes" : "No"}</span>
                <span>
                  Arrival age: {impact.minutes_since_arrival.toFixed(1)} minutes
                </span>
                <span>
                  Address type: {impact.address_type} (
                  {(impact.address_type_confidence * 100).toFixed(0)}%)
                </span>
              </div>
              <p>
                Freeze{" "}
                <Amount
                  value={amount || impact.traceable_amount}
                  asset={impact.target}
                />{" "}
                of estimated{" "}
                <Amount value={impact.est_balance} asset={impact.target} />
              </p>
              <label>
                Traceable funds to request (%)
                <input
                  type="range"
                  aria-label="Freeze share"
                  min="0"
                  max="10000"
                  step="1"
                  value={Number(
                    (BigInt(
                      /^\d+$/.test(amount) ? amount : impact.traceable_amount,
                    ) *
                      10000n) /
                      (BigInt(impact.max_freeze_amount) || 1n),
                  )}
                  onChange={(e) =>
                    setAmount(
                      (
                        (BigInt(impact.max_freeze_amount) *
                          BigInt(e.target.value)) /
                        10000n
                      ).toString(),
                    )
                  }
                  disabled={!impact.freeze_allowed}
                />
              </label>
              <label>
                Freeze amount (exact base units)
                <input
                  aria-label="Freeze amount"
                  inputMode="numeric"
                  pattern="[0-9]{1,38}"
                  value={amount || impact.traceable_amount}
                  onChange={(e) => {
                    if (/^\d{0,38}$/.test(e.target.value))
                      setAmount(e.target.value);
                  }}
                  disabled={!impact.freeze_allowed}
                />
              </label>
              <p className="muted">{impact.disclaimer}</p>
              <Explain items={impact.evidence} />
              <button onClick={() => setStep("Approval")}>
                Review request package
              </button>
            </>
          )}
        </section>
      )}
      {step === "Approval" && (
        <section className="panel">
          <h3>Freeze and disclosure requests</h3>
          {impact && (
            <>
              <p>
                {impact.target.chain} · {impact.target.address} ·{" "}
                {impact.impact_level} impact
              </p>
              <Explain items={impact.evidence} />
            </>
          )}
          {user.role === "INVESTIGATOR" && (
            <form
              className="inline"
              onSubmit={(e) => {
                e.preventDefault();
                run(() =>
                  api(`/cases/${caseId}/requests`, {
                    kind,
                    amount:
                      kind === "DISCLOSURE"
                        ? null
                        : amount || impact?.traceable_amount,
                    mock_outcome: "delayed",
                  }),
                );
              }}
            >
              <label>
                Request kind
                <select
                  aria-label="Request kind"
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                >
                  <option>DISCLOSURE</option>
                  <option disabled={!impact?.freeze_allowed}>FREEZE</option>
                  <option disabled={!impact?.freeze_allowed}>BOTH</option>
                </select>
              </label>
              <button
                disabled={
                  busy ||
                  !impact ||
                  impact.stale ||
                  (kind !== "DISCLOSURE" && !impact.freeze_allowed)
                }
              >
                Create request draft
              </button>
            </form>
          )}
          {data?.requests.map((request) => (
            <article className="request-card" key={request.id}>
              <Link to={`/cases/${caseId}/requests/${request.id}`}>
                {request.kind} · {request.state}
              </Link>
              <p>{request.target_address}</p>
              <RequestActions
                request={request}
                user={user}
                onChange={() =>
                  client.invalidateQueries({ queryKey: ["workspace", caseId] })
                }
              />
            </article>
          ))}
        </section>
      )}
      {step === "Report" && (
        <section className="panel">
          <h3>Sealed evidence reports</h3>
          {user.role === "INVESTIGATOR" && (
            <button
              disabled={busy || !graph || graph.status !== "COMPLETED"}
              onClick={() => run(() => api(`/cases/${caseId}/reports`, {}))}
            >
              Generate report
            </button>
          )}
          {!data?.reports.length && (
            <p>
              No sealed report yet. Each export preserves evidence as of its
              report date.
            </p>
          )}
          {data?.reports.map((report) => (
            <ReportCard key={report.id} report={report} />
          ))}
        </section>
      )}
      {step === "Forecast" && (
        <section className="panel">
          <h3>Forecast cone</h3>
          <p className="badge">Trained on simulated paths</p>
          {user.role === "ADMIN" && (
            <button
              disabled={busy}
              onClick={() =>
                run(async () =>
                  setTraining(await api("/dev/forecast/train", {})),
                )
              }
            >
              Train forecast model
            </button>
          )}
          {training && <pre>{JSON.stringify(training, null, 2)}</pre>}
          {user.role === "INVESTIGATOR" && (
            <button
              disabled={busy || !graph}
              onClick={() =>
                run(() =>
                  api(`/cases/${caseId}/forecast`, {
                    rollouts: 1000,
                    seed: 42,
                  }),
                )
              }
            >
              Run forecast
            </button>
          )}
          {data?.forecast && (
            <>
              <p>{data.forecast.disclaimer}</p>
              {data.forecast.exit_entity_probs.map((exit) => (
                <div key={exit.entity_id}>
                  <strong>
                    {exit.entity} · {(exit.probability * 100).toFixed(1)}%
                  </strong>
                  <progress max={1} value={exit.probability} />
                  <small>
                    Global {(exit.global_probability * 100).toFixed(1)}% · Gang{" "}
                    {exit.gang_probability === null
                      ? "insufficient history"
                      : (exit.gang_probability * 100).toFixed(1) + "%"}
                  </small>
                </div>
              ))}
              <p>
                ETA range:{" "}
                {((data.forecast.eta_quantiles_seconds.p10 || 0) / 60).toFixed(
                  1,
                )}
                –
                {((data.forecast.eta_quantiles_seconds.p90 || 0) / 60).toFixed(
                  1,
                )}{" "}
                minutes, conditional on reaching an exit.
              </p>
              <p>
                Unresolved probability:{" "}
                {(data.forecast.unresolved_probability * 100).toFixed(1)}%
              </p>
              <Explain items={data.forecast.evidence} />
              <details>
                <summary>Held-out synthetic evaluation</summary>
                <pre>{JSON.stringify(data.forecast.evaluation, null, 2)}</pre>
              </details>
              {user.role === "INVESTIGATOR" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    run(() => api(`/cases/${caseId}/forecast/prestage`, {}))
                  }
                >
                  Pre-stage draft only
                </button>
              )}
            </>
          )}
        </section>
      )}
    </section>
  );
}
export function RequestActions({
  request,
  user,
  onChange,
}: {
  request: RequestRecord;
  user: User;
  onChange: () => unknown;
}) {
  const [reason, setReason] = useState(""),
    [code, setCode] = useState(""),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false),
    [clock, setClock] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  async function act(action: string) {
    setBusy(true);
    setError(undefined);
    try {
      if (["approve", "confirm", "release"].includes(action))
        await api("/auth/2fa/verify", { code });
      await api(`/requests/${request.id}/${action}`, {
        reason,
        ...(action === "golden-hour" ? { minutes: 30 } : {}),
      });
      onChange();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const pending = ["DRAFT", "RETURNED"].includes(request.state),
    review = request.state === "PROPOSED",
    active = ["SENT", "ACKNOWLEDGED", "FROZEN"].includes(request.state);
  return (
    <div>
      <ErrorMessage error={error} />
      {request.golden_hour &&
        !request.approver_id &&
        request.second_key_due && (
          <p className="golden-banner">
            Temporary hold: second key due in{" "}
            {Math.max(
              0,
              Math.ceil(
                (new Date(request.second_key_due).getTime() - clock) / 1000,
              ),
            )}{" "}
            seconds. It lapses without confirmation.
          </p>
        )}
      {request.expires_at && (
        <p>Expiry: {new Date(request.expires_at).toLocaleString()}</p>
      )}
      {["INVESTIGATOR", "SUPERVISOR"].includes(user.role) && (
        <label>
          Written justification
          <textarea
            aria-label="Written justification"
            minLength={10}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
      )}
      {user.role === "SUPERVISOR" && (
        <label>
          Authenticator code
          <input
            aria-label="Approval authenticator code"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
      )}
      <div className="inline">
        {user.role === "INVESTIGATOR" && pending && (
          <button
            disabled={
              busy || reason.length < 10 || !!request.package.forecast_only
            }
            onClick={() => act("propose")}
          >
            Propose request
          </button>
        )}
        {user.role === "INVESTIGATOR" &&
          (pending || review) &&
          request.kind !== "DISCLOSURE" && (
            <button
              disabled={busy || reason.length < 10}
              onClick={() => act("golden-hour")}
            >
              Start golden-hour hold
            </button>
          )}
        {user.role === "SUPERVISOR" &&
          review &&
          ["approve", "reject", "return"].map((action) => (
            <button
              key={action}
              disabled={busy || reason.length < 10}
              onClick={() => act(action)}
            >
              {action === "approve"
                ? "Approve with second key"
                : action === "reject"
                  ? "Reject request"
                  : "Return for evidence"}
            </button>
          ))}
        {user.role === "SUPERVISOR" &&
          active &&
          request.golden_hour &&
          !request.approver_id && (
            <button disabled={busy} onClick={() => act("confirm")}>
              Confirm temporary hold
            </button>
          )}
        {user.role === "SUPERVISOR" && active && (
          <button disabled={busy} onClick={() => act("release")}>
            Release request
          </button>
        )}
        {request.dispatch_error && user.role === "INVESTIGATOR" && (
          <button
            disabled={busy}
            onClick={async () => {
              try {
                await api(`/requests/${request.id}/retry`, {});
                onChange();
              } catch (e) {
                setError(e);
              }
            }}
          >
            Retry authorized dispatch
          </button>
        )}
      </div>
      {request.dispatch_error && (
        <p role="alert">{request.dispatch_error.message}</p>
      )}
    </div>
  );
}
export function RequestPage({ user }: { user: User }) {
  const { rid, id } = useParams();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["request", rid],
    queryFn: () => api<RequestRecord>(`/requests/${rid}`),
    refetchInterval: 1500,
  });
  return (
    <>
      <Link to={`/cases/${id}`}>Back to case</Link>
      <h1>Request timeline</h1>
      <ErrorMessage error={query.error} />
      {query.isPending && <p>Loading request…</p>}
      {query.data && (
        <section className="panel">
          <h2>
            {query.data.kind} · {query.data.state}
          </h2>
          <p>
            {query.data.target_chain} · {query.data.target_address}
          </p>
          <Explain items={[query.data.package]} />
          <RequestActions
            request={query.data}
            user={user}
            onChange={() =>
              client.invalidateQueries({ queryKey: ["request", rid] })
            }
          />
          <ol>
            {query.data.timeline.map((event, index) => (
              <li key={index}>
                <strong>
                  {event.from || "Created"} → {event.to}
                </strong>
                <p>
                  {event.note} · {new Date(event.at).toLocaleString()}
                </p>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
function ReportCard({ report }: { report: ReportRecord }) {
  const [proof, setProof] = useState<unknown>(),
    [error, setError] = useState<unknown>();
  return (
    <article className="report-card">
      <h4>Report version {report.version}</h4>
      <p>{new Date(report.created_at).toLocaleString()}</p>
      <code>{report.bundle_hash}</code>
      <div className="inline">
        <a
          target="_blank"
          rel="noreferrer"
          href={`/api/v1/reports/${report.id}/pdf`}
        >
          Download PDF
        </a>
        <a
          target="_blank"
          rel="noreferrer"
          href={`/api/v1/reports/${report.id}/bundle.json`}
        >
          Export JSON
        </a>
        <Link to={`/verify/${report.bundle_hash}`}>Verify seal</Link>
      </div>
      <label>
        Evidence inclusion proof
        <select
          aria-label="Evidence inclusion proof"
          defaultValue=""
          onChange={async (e) => {
            if (!e.target.value) return;
            try {
              setProof(
                await api(`/reports/${report.id}/proof/${e.target.value}`),
              );
            } catch (e) {
              setError(e);
            }
          }}
        >
          <option value="">Choose evidence item</option>
          {report.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.kind}
            </option>
          ))}
        </select>
      </label>
      <ErrorMessage error={error} />
      {proof !== undefined && <pre>{JSON.stringify(proof, null, 2)}</pre>}
    </article>
  );
}
export function VerificationPage() {
  const { hash } = useParams();
  const [uploaded, setUploaded] = useState<boolean | null>(null),
    [proof, setProof] = useState(""),
    [proofValid, setProofValid] = useState<boolean | null>(null),
    [error, setError] = useState<unknown>();
  const query = useQuery({
    queryKey: ["verification", hash],
    queryFn: () =>
      api<{
        valid: boolean;
        created_at: string;
        version: number;
        merkle_root: string;
        item_count: number;
        note: string;
      }>(`/verify/${hash}`),
    retry: false,
  });
  async function verifyProof() {
    try {
      const data = JSON.parse(proof) as {
        leaf: string;
        path: { side: string; hash: string }[];
        root: string;
      };
      const bytes = (hex: string) => {
        if (!/^[a-f0-9]{64}$/.test(hex))
          throw new Error("Expected a SHA-256 hexadecimal hash");
        return Uint8Array.from(hex.match(/.{2}/g) || [], (v) =>
          parseInt(v, 16),
        );
      };
      let value = bytes(data.leaf);
      for (const item of data.path) {
        if (!["left", "right"].includes(item.side))
          throw new Error("Invalid proof direction");
        const sibling = bytes(item.hash);
        const joined = new Uint8Array(64);
        if (item.side === "left") {
          joined.set(sibling);
          joined.set(value, 32);
        } else {
          joined.set(value);
          joined.set(sibling, 32);
        }
        value = new Uint8Array(await crypto.subtle.digest("SHA-256", joined));
      }
      const root = Array.from(value, (v) =>
        v.toString(16).padStart(2, "0"),
      ).join("");
      setProofValid(root === query.data?.merkle_root && root === data.root);
    } catch (e) {
      setError(e);
      setProofValid(false);
    }
  }
  return (
    <main className="public-verification">
      <Link to="/cases">SAHYOG</Link>
      <h1>Evidence verification</h1>
      <ErrorMessage error={error || query.error} />
      {query.isPending && <p>Checking published seal…</p>}
      {query.data && (
        <section className="panel">
          <h2>
            {query.data.valid
              ? "Verified, unchanged"
              : "Integrity check failed"}
          </h2>
          <p>
            Version {query.data.version} ·{" "}
            {new Date(query.data.created_at).toLocaleString()} ·{" "}
            {query.data.item_count} evidence items
          </p>
          <code>{hash}</code>
          <p>{query.data.note}</p>
          <label>
            Check an exported JSON bundle
            <input
              aria-label="Evidence bundle file"
              type="file"
              accept="application/json"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (file.size > 10 * 1024 * 1024) {
                  setError(new Error("Bundle file exceeds 10 MB"));
                  return;
                }
                try {
                  const result = await api<{ valid: boolean }>(
                    `/verify/${hash}`,
                    JSON.parse(await file.text()),
                  );
                  setUploaded(result.valid);
                } catch (e) {
                  setError(e);
                }
              }}
            />
          </label>
          {uploaded !== null && (
            <p>
              {uploaded
                ? "Uploaded bundle matches seal"
                : "Uploaded bundle differs from seal"}
            </p>
          )}
          <h3>Proof viewer</h3>
          <label>
            Inclusion proof JSON
            <textarea
              aria-label="Inclusion proof JSON"
              value={proof}
              onChange={(e) => setProof(e.target.value)}
            />
          </label>
          <button onClick={verifyProof}>Verify item inclusion</button>
          {proofValid !== null && (
            <p>
              {proofValid
                ? "Item verified in bundle"
                : "Item proof does not match this published root"}
            </p>
          )}
        </section>
      )}
    </main>
  );
}
export function AnalyticsPage() {
  const query = useQuery({
    queryKey: ["analytics"],
    queryFn: () =>
      api<{
        case_count: number;
        statuses: Record<string, number>;
        assets: (Asset & { traced: string; frozen: string })[];
        average_time_to_attribution_seconds: number | null;
        time_to_attribution: { case_ref: string; seconds: number }[];
        evidence: Evidence[];
      }>("/analytics/overview"),
  });
  const vasps = useQuery({
    queryKey: ["analytics-vasps"],
    queryFn: () =>
      api<
        {
          name: string;
          responsiveness_ewma: number;
          avg_response_s: number;
          response_rate: number;
        }[]
      >("/analytics/vasps"),
  });
  return (
    <>
      <h1>Case analytics</h1>
      <ErrorMessage error={query.error} />
      {query.data && (
        <section className="panel">
          <div className="metric-grid">
            <strong>{query.data.case_count} cases</strong>
            <strong>
              {query.data.average_time_to_attribution_seconds === null
                ? "Not attributed yet"
                : (query.data.average_time_to_attribution_seconds / 60).toFixed(
                    1,
                  ) + " minutes average attribution"}
            </strong>
            {Object.entries(query.data.statuses).map(([status, count]) => (
              <span key={status}>
                {status}: {count}
              </span>
            ))}
          </div>
          {query.data.assets.map((asset, i) => (
            <p key={i}>
              {asset.chain} · Traced{" "}
              <Amount value={asset.traced} asset={asset} /> · Frozen{" "}
              <Amount value={asset.frozen} asset={asset} />
            </p>
          ))}
          <h3>Time to attribution</h3>
          {query.data.time_to_attribution.map((r) => (
            <div key={r.case_ref}>
              {r.case_ref} · {(r.seconds / 60).toFixed(1)} minutes
              <progress
                max={Math.max(
                  ...query.data!.time_to_attribution.map((r) => r.seconds),
                  1,
                )}
                value={r.seconds}
              />
            </div>
          ))}
          <Explain items={query.data.evidence} />
        </section>
      )}
      <section className="panel">
        <h2>VASP response analytics</h2>
        {vasps.data?.map((v) => (
          <div key={v.name}>
            {v.name} · {(v.response_rate * 100).toFixed(0)}% responses ·{" "}
            {v.avg_response_s.toFixed(2)}s
            <progress max={1} value={v.responsiveness_ewma} />
          </div>
        ))}
      </section>
    </>
  );
}
export function AuditPage({ user }: { user: User }) {
  const [entity, setEntity] = useState(""),
    [integrity, setIntegrity] = useState<{
      valid: boolean;
      checked: number;
      first_mismatch?: string;
    }>(),
    [error, setError] = useState<unknown>();
  const rows = useQuery({
    queryKey: ["audit", entity],
    queryFn: () =>
      api<
        {
          id: number;
          action: string;
          entity: string;
          entry_hash: string;
          prev_hash: string;
          at: string;
          payload: unknown;
        }[]
      >("/audit" + (entity ? "?entity=" + encodeURIComponent(entity) : "")),
  });
  return (
    <>
      <h1>Hash-chained audit</h1>
      <ErrorMessage error={error || rows.error} />
      {user.role === "ADMIN" && (
        <button
          onClick={async () => {
            try {
              setIntegrity(await api("/audit/verify"));
            } catch (e) {
              setError(e);
            }
          }}
        >
          Verify audit chain
        </button>
      )}
      {integrity && (
        <p className="badge">
          {integrity.valid ? "Chain intact" : "Tampering detected"} ·{" "}
          {integrity.checked} entries
          {integrity.first_mismatch && " · " + integrity.first_mismatch}
        </p>
      )}
      <label>
        Entity filter
        <input
          aria-label="Audit entity filter"
          value={entity}
          onChange={(e) => setEntity(e.target.value)}
        />
      </label>
      {rows.data?.map((r) => (
        <details key={r.id}>
          <summary>
            {r.id}. {r.action} · {new Date(r.at).toLocaleString()}
          </summary>
          <p>
            Previous: <code>{r.prev_hash}</code>
          </p>
          <p>
            Entry: <code>{r.entry_hash}</code>
          </p>
          <pre>{JSON.stringify(r.payload, null, 2)}</pre>
        </details>
      ))}
    </>
  );
}
export function NotificationBell() {
  const client = useQueryClient();
  const rows = useQuery({
    queryKey: ["notifications"],
    queryFn: () =>
      api<
        {
          id: number;
          request_id: string | null;
          kind: string;
          read_at: string | null;
          payload: { case_id?: string; collision_id?: string };
        }[]
      >("/notifications"),
    refetchInterval: 5000,
  });
  const { t } = useLanguage();
  useEffect(() => {
    let socket: WebSocket | undefined,
      timer: ReturnType<typeof setTimeout> | undefined,
      stopped = false,
      cursor = 0;
    function connect() {
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws/notifications?after=${cursor}`,
      );
      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.id) {
          cursor = data.id;
          client.invalidateQueries({ queryKey: ["notifications"] });
        }
      };
      socket.onclose = (event) => {
        if (!stopped && event.code !== 4401) timer = setTimeout(connect, 1500);
      };
    }
    const start = setTimeout(connect, 0);
    return () => {
      stopped = true;
      clearTimeout(start);
      clearTimeout(timer);
      socket?.close();
    };
  }, [client]);
  return (
    <details className="notification-bell">
      <summary>
        {t("Notifications")} ({rows.data?.filter((n) => !n.read_at).length || 0}
        )
      </summary>
      <div>
        {rows.data?.map((n) => (
          <article key={n.id}>
            {n.request_id && n.payload.case_id ? (
              <Link to={`/cases/${n.payload.case_id}/requests/${n.request_id}`}>
                {n.kind.replaceAll("_", " ")}
              </Link>
            ) : (
              <Link
                to={
                  n.payload.case_id
                    ? `/cases/${n.payload.case_id}`
                    : "/collisions"
                }
              >
                {n.kind}
              </Link>
            )}
            {!n.read_at && (
              <button
                onClick={async () => {
                  await api(`/notifications/${n.id}/read`, {});
                  client.invalidateQueries({ queryKey: ["notifications"] });
                }}
              >
                Mark read
              </button>
            )}
          </article>
        ))}
      </div>
    </details>
  );
}
export function CollisionsPage() {
  const [error, setError] = useState<unknown>(),
    [text, setText] = useState("");
  const client = useQueryClient();
  const rows = useQuery({
    queryKey: ["collisions"],
    queryFn: () =>
      api<
        {
          id: string;
          own_case_id: string;
          status: string;
          room: {
            units: string[];
            messages: {
              id: string;
              unit: string;
              author: string;
              text: string;
              at: string;
            }[];
          } | null;
        }[]
      >("/collisions"),
    refetchInterval: 5000,
  });
  return (
    <>
      <h1>Cross-unit collisions</h1>
      <p>
        Overlap notices conceal the other complaint's details. Coordination
        rooms are restricted to participating units.
      </p>
      <ErrorMessage error={error || rows.error} />
      {rows.data?.length === 0 && (
        <p>No overlapping infrastructure detected.</p>
      )}
      {rows.data?.map((r) => (
        <section className="panel" key={r.id}>
          <Link to={`/cases/${r.own_case_id}`}>Your case</Link> · {r.status}
          {!r.room ? (
            <button
              onClick={async () => {
                try {
                  await api(`/collisions/${r.id}/open-room`, {});
                  client.invalidateQueries({ queryKey: ["collisions"] });
                } catch (e) {
                  setError(e);
                }
              }}
            >
              Open secure coordination room
            </button>
          ) : (
            <>
              <p>Units: {r.room.units.join(" / ")}</p>
              {r.room.messages.map((m) => (
                <p key={m.id}>
                  {m.unit} · {m.author}: {m.text}
                </p>
              ))}
              <label>
                Coordination message
                <textarea
                  aria-label="Coordination message"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </label>
              <button
                onClick={async () => {
                  try {
                    await api(`/collisions/${r.id}/room`, { reason: text });
                    setText("");
                    client.invalidateQueries({ queryKey: ["collisions"] });
                  } catch (e) {
                    setError(e);
                  }
                }}
              >
                Add message
              </button>
            </>
          )}
        </section>
      ))}
    </>
  );
}
export function GangPage({ user }: { user: User }) {
  const { id } = useParams();
  const [amount, setAmount] = useState("0"),
    [split, setSplit] = useState<unknown>(),
    [error, setError] = useState<unknown>();
  const query = useQuery({
    queryKey: ["gang", id],
    queryFn: () =>
      api<{
        id: string;
        name: string;
        cases: { id: string; title: string; case_ref: string }[];
        targets: (Asset & { by_case: Record<string, string>; total: string })[];
        evidence: Evidence[];
      }>(`/gang-cases/${id}`),
  });
  const network = useQuery({
    queryKey: ["communities", id],
    queryFn: () =>
      api<{
        communities: string[][];
        nodes: { id: string; kind: string }[];
        edges: { source: string; target: string }[];
      }>(`/gang-cases/${id}/communities`),
  });
  useEffect(() => {
    if (!network.data) return;
    const container = document.getElementById("gang-network");
    if (!container) return;
    const data = network.data;
    const cy = cytoscape({
      container,
      elements: [
        ...data.nodes.map((n) => ({
          data: {
            ...n,
            colour:
              colours[
                data.communities.findIndex((g) => g.includes(n.id)) %
                  colours.length
              ],
          },
        })),
        ...data.edges.map((e, i) => ({ data: { ...e, id: "gang-edge:" + i } })),
      ],
      style: [
        {
          selector: "node",
          style: { "background-color": "data(colour)", label: "data(kind)" },
        },
        { selector: "edge", style: { "line-color": "#a3b7c5" } },
      ],
      layout: { name: "cose", animate: false },
    });
    return () => cy.destroy();
  }, [network.data]);
  return (
    <>
      <h1>Gang-level investigation</h1>
      <ErrorMessage error={error || query.error || network.error} />
      {query.data && (
        <section className="panel">
          <h2>{query.data.name}</h2>
          {query.data.cases.map((c) => (
            <p key={c.id}>
              <Link to={`/cases/${c.id}`}>
                {c.case_ref} · {c.title}
              </Link>
            </p>
          ))}
          {query.data.targets.map((target, i) => (
            <p key={i}>
              {target.chain} · <Amount value={target.total} asset={target} /> ·{" "}
              {target.address}
            </p>
          ))}
          <Explain items={query.data.evidence} />
          {user.role === "INVESTIGATOR" && (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  setSplit(
                    await api(`/gang-cases/${id}/split-preview`, {
                      available_amount: amount,
                    }),
                  );
                } catch (e) {
                  setError(e);
                }
              }}
            >
              <label>
                Available restitution (base units)
                <input
                  aria-label="Available restitution"
                  pattern="[0-9]{1,38}"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </label>
              <button>Preview restitution split</button>
            </form>
          )}
          {split !== undefined && <pre>{JSON.stringify(split, null, 2)}</pre>}
        </section>
      )}
      <section className="panel">
        <h2>Candidate gang communities</h2>
        <div id="gang-network" className="case-graph" />
        {network.data?.communities.map((group, i) => (
          <details key={i}>
            <summary>
              Community {i + 1} · {group.length} members
            </summary>
            {group.map((node) => (
              <p key={node}>{node}</p>
            ))}
          </details>
        ))}
      </section>
    </>
  );
}
