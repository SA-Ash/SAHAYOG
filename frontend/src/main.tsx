import { HealthPage, BenchmarkReportPage } from "./monitoring";
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  Link,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { z } from "zod";
import "./style.css";
import { api } from "./api";
import { LanguageProvider, LanguageToggle, useLanguage } from "./language";
import {
  CaseWorkspace,
  RequestPage,
  GangPage,
  AnalyticsPage,
  AuditPage,
  VerificationPage,
  NotificationBell,
  CollisionsPage,
} from "./workflow";
import {
  ChainsPage,
  ScenarioPage,
  ProbeMapPage,
  VaspDirectoryPage,
  LabelsPage,
} from "./intelligence";

type User = {
  name: string;
  email: string;
  role: string;
  totp_enabled: boolean;
};
type Tx = {
  chain: string;
  tx_hash?: string;
  victim_address?: string;
  suspect_address?: string;
  token?: string;
  amount?: string;
  decimals?: number;
  tx_time?: string;
  status?: string;
};
type Case = {
  id: string;
  case_ref: string;
  title: string;
  sahyog_ref?: string;
  status: string;
  source: string;
  created_at: string;
  transactions: Tx[];
  attachments: { id: string; sha256: string }[];
  scenario_id?: string;
};
type Candidate = {
  type: string;
  value: string;
  checksum_ok: boolean | null;
  confidence: number;
  source: string;
};
type Extraction = {
  attachment_id: string;
  candidates: Candidate[];
  notices: string[];
};
const queryClient = new QueryClient();
function ErrorText({ error }: { error: unknown }) {
  return error ? (
    <p role="alert" className="error">
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;
}
function SimulatedBadge({ source }: { source: string }) {
  return ["simulated", "demo_seed"].includes(source) ? (
    <span className="badge">Simulated · {source}</span>
  ) : (
    <span className="badge">{source}</span>
  );
}
function LoginPage() {
  const navigate = useNavigate(),
    client = useQueryClient();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  return (
    <main className="login panel">
      <h1>SAHYOG</h1>
      <p>Blockchain complaint intake</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          const f = new FormData(e.currentTarget);
          try {
            await api("/auth/login", Object.fromEntries(f));
            await client.invalidateQueries({ queryKey: ["me"] });
            navigate("/cases");
          } catch (e) {
            setError(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Email
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        <ErrorText error={error} />
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className="muted">
        Demo accounts: investigator, supervisor or admin @sahyog.demo. Use the
        configured DEMO_PASSWORD.
      </p>
    </main>
  );
}
function TwoFactorPrompt({ user }: { user: User }) {
  const [open, setOpen] = useState(false),
    [setup, setSetup] = useState<{
      secret: string;
      provisioning_uri: string;
    }>(),
    [error, setError] = useState<unknown>(),
    [message, setMessage] = useState("");
  const client = useQueryClient();
  return (
    <>
      <button className="secondary" onClick={() => setOpen(!open)}>
        Authenticator
      </button>
      {open && (
        <section className="panel">
          <h2>
            {user.totp_enabled
              ? "Verify authenticator"
              : "Set up authenticator"}
          </h2>
          <p>
            Verifying your authenticator elevates this session for five minutes.
          </p>
          {!user.totp_enabled && !setup && (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  setSetup(
                    await api("/auth/2fa/setup", {
                      password: new FormData(e.currentTarget).get("password"),
                    }),
                  );
                  setError(undefined);
                } catch (e) {
                  setError(e);
                }
              }}
            >
              <label>
                Current password
                <input name="password" type="password" required />
              </label>
              <button>Start setup</button>
            </form>
          )}
          {setup && (
            <>
              <p>Add this secret to your authenticator:</p>
              <code>{setup.secret}</code>
              <p>
                <a href={setup.provisioning_uri}>Open authenticator</a>
              </p>
            </>
          )}
          {(user.totp_enabled || setup) && (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await api(
                    user.totp_enabled
                      ? "/auth/2fa/verify"
                      : "/auth/2fa/confirm",
                    { code: new FormData(e.currentTarget).get("code") },
                  );
                  setMessage(
                    "Authenticator verified. Session elevated for five minutes.",
                  );
                  setSetup(undefined);
                  setError(undefined);
                  await client.invalidateQueries({ queryKey: ["me"] });
                } catch (e) {
                  setError(e);
                }
              }}
            >
              <label>
                Six-digit code
                <input
                  name="code"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  autoComplete="one-time-code"
                  required
                />
              </label>
              <button>Verify</button>
            </form>
          )}
          <ErrorText error={error} />
          <p role="status">{message}</p>
        </section>
      )}
    </>
  );
}
function AppShell() {
  const { t } = useLanguage();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/auth/me"),
    retry: false,
  });
  const navigate = useNavigate();
  if (me.isPending) return <p>Loading session…</p>;
  if (!me.data) return <Navigate to="/login" replace />;
  const user = me.data;
  return (
    <>
      <header>
        <Link to="/cases" className="brand">
          SAHYOG
        </Link>
        <span>
          {user.name} <span className="badge">{user.role}</span>
        </span>
        <LanguageToggle />
        <NotificationBell />
        <button
          className="secondary"
          onClick={async () => {
            try {
              await api("/auth/logout", {});
              queryClient.clear();
              navigate("/login");
            } catch (e) {
              alert(e instanceof Error ? e.message : "Logout failed");
            }
          }}
        >
          {t("Sign out")}
        </button>
      </header>
      <div className="workspace">
        <aside>
          <nav>
            <Link to="/cases">{t("Cases")}</Link>
            <Link to="/chains">{t("Chain lookup")}</Link>
            <Link to="/dev/scenarios">{t("Scenarios")}</Link>
            <Link to="/probes">{t("Probe map")}</Link>
            <Link to="/vasps">{t("VASP directory")}</Link>
            <Link to="/labels">{t("Labels")}</Link>
            <Link to="/health">Health</Link>
            <Link to="/benchmarks">Benchmarks</Link>
            <Link to="/analytics">{t("Analytics")}</Link>
            <Link to="/collisions">Cross-unit collisions</Link>
            {["ADMIN", "SUPERVISOR"].includes(user.role) && (
              <Link to="/audit">{t("Audit")}</Link>
            )}
          </nav>
          <p className="muted">Complaint intake and review</p>
          <TwoFactorPrompt user={user} />
        </aside>
        <main>
          <Routes>
            <Route path="/cases" element={<CaseListPage user={user} />} />
            <Route path="/cases/:id" element={<CaseDetail user={user} />} />
            <Route
              path="/cases/:id/requests/:rid"
              element={<RequestPage user={user} />}
            />
            <Route path="/gang/:id" element={<GangPage user={user} />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/benchmarks" element={<BenchmarkReportPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
            <Route path="/audit" element={<AuditPage user={user} />} />
            <Route path="/collisions" element={<CollisionsPage />} />
            <Route path="/chains" element={<ChainsPage user={user} />} />
            <Route
              path="/dev/scenarios"
              element={<ScenarioPage user={user} />}
            />
            <Route path="/probes" element={<ProbeMapPage user={user} />} />
            <Route path="/vasps" element={<VaspDirectoryPage />} />
            <Route path="/labels" element={<LabelsPage user={user} />} />
            <Route path="*" element={<Navigate to="/cases" replace />} />
          </Routes>
        </main>
      </div>
    </>
  );
}
const chains = ["ethereum", "bnb", "polygon", "tron", "bitcoin"];
function CaseListPage({ user }: { user: User }) {
  const [filters, setFilters] = useState({
      status: "",
      chain: "",
      date_from: "",
      date_to: "",
      search: "",
    }),
    [page, setPage] = useState(1),
    [create, setCreate] = useState(false),
    [ref, setRef] = useState(""),
    [error, setError] = useState<unknown>(),
    [importing, setImporting] = useState(false);
  const navigate = useNavigate();
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v),
  );
  params.set("page", String(page));
  const cases = useQuery({
    queryKey: ["cases", filters, page],
    queryFn: () =>
      api<{ items: Case[]; total: number; page_size: number }>(
        "/cases?" + params,
      ),
  });
  return (
    <>
      <div className="heading">
        <div>
          <h1>Cases</h1>
          <p>Review complaints and record victim transactions.</p>
        </div>
        {user.role === "INVESTIGATOR" && (
          <button onClick={() => setCreate(true)}>New case</button>
        )}
      </div>
      <section className="panel filters">
        {(["search", "status", "chain", "date_from", "date_to"] as const).map(
          (key) => (
            <label key={key}>
              {key.replaceAll("_", " ")}
              {["status", "chain"].includes(key) ? (
                <select
                  value={filters[key]}
                  onChange={(e) => {
                    setFilters({ ...filters, [key]: e.target.value });
                    setPage(1);
                  }}
                >
                  <option value="">All</option>
                  {(key === "chain"
                    ? chains
                    : [
                        "OPEN",
                        "TRACING",
                        "ATTRIBUTED",
                        "FREEZE_PENDING",
                        "CLOSED",
                      ]
                  ).map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              ) : (
                <input
                  type={key.startsWith("date") ? "date" : "search"}
                  value={filters[key]}
                  onChange={(e) => {
                    setFilters({ ...filters, [key]: e.target.value });
                    setPage(1);
                  }}
                />
              )}
            </label>
          ),
        )}
      </section>
      {user.role === "INVESTIGATOR" && (
        <form
          className="panel inline"
          onSubmit={async (e) => {
            e.preventDefault();
            setImporting(true);
            try {
              const c = await api<Case>(
                "/integrations/sahyog/complaints/" +
                  encodeURIComponent(ref) +
                  "/import",
                {},
              );
              navigate("/cases/" + c.id);
            } catch (e) {
              setError(e);
            } finally {
              setImporting(false);
            }
          }}
        >
          <label>
            SAHYOG complaint reference
            <input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              required
            />
          </label>
          <button disabled={importing}>Import complaint</button>
          <ErrorText error={error} />
        </form>
      )}
      <ErrorText error={cases.error} />
      {cases.isPending ? (
        <p>Loading cases…</p>
      ) : (
        <section className="panel tablewrap">
          <table>
            <thead>
              <tr>
                <th>Reference / title</th>
                <th>Status</th>
                <th>Chain</th>
                <th>Created</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {cases.data?.items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={"/cases/" + c.id}>{c.case_ref}</Link>
                    <div>{c.title}</div>
                  </td>
                  <td>{c.status}</td>
                  <td>
                    {[...new Set(c.transactions.map((t) => t.chain))].join(
                      ", ",
                    )}
                  </td>
                  <td>{new Date(c.created_at).toLocaleDateString()}</td>
                  <td>
                    <SimulatedBadge source={c.source} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {cases.data?.items.length === 0 && (
            <p>No cases match these filters.</p>
          )}
          <div className="inline">
            <button disabled={page === 1} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>
              Page {page} · {cases.data?.total ?? 0} cases
            </span>
            <button
              disabled={
                !cases.data || page * cases.data.page_size >= cases.data.total
              }
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
        </section>
      )}
      {create && (
        <NewCaseDialog
          onClose={() => setCreate(false)}
          onDone={(c) => navigate("/cases/" + c.id)}
        />
      )}
    </>
  );
}
const transactionSchema = z
  .object({
    chain: z.enum(["ethereum", "bnb", "polygon", "tron", "bitcoin"]),
    tx_hash: z
      .string()
      .regex(/^(0x)?[a-fA-F0-9]{64}$/, "Hash must be 64 hexadecimal characters")
      .optional(),
    suspect_address: z.string().optional(),
    victim_address: z.string().optional(),
    token: z
      .string()
      .regex(/^[A-Z0-9._-]{1,20}$/)
      .optional(),
    amount: z
      .string()
      .regex(/^[0-9]{1,38}$/, "Amount must be an integer in base units")
      .optional(),
    decimals: z.number().int().min(0).max(30).optional(),
    tx_time: z.string().optional(),
  })
  .superRefine((t, c) => {
    if (!t.tx_hash && !t.suspect_address)
      c.addIssue({
        code: "custom",
        message: "Provide a hash or suspect address",
      });
    if (
      t.amount &&
      (!t.token || t.decimals === undefined || BigInt(t.amount) === 0n)
    )
      c.addIssue({
        code: "custom",
        message: "Positive amount requires token and decimals",
      });
  });
function TransactionFields({
  tx,
  setTx,
}: {
  tx: Record<string, string>;
  setTx: (v: Record<string, string>) => void;
}) {
  return (
    <div className="fields">
      <label>
        Chain
        <select
          value={tx.chain}
          onChange={(e) => setTx({ ...tx, chain: e.target.value })}
        >
          {chains.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </label>
      {[
        ["tx_hash", "Transaction hash"],
        ["victim_address", "Victim wallet"],
        ["suspect_address", "Suspect wallet"],
        ["token", "Token (e.g. USDT)"],
        ["amount", "Amount in integer base units"],
        ["decimals", "Token decimals"],
        ["tx_time", "Transaction time (ISO 8601 with timezone)"],
      ].map(([key, label]) => (
        <label key={key}>
          {label}
          <input
            value={tx[key] || ""}
            onChange={(e) => setTx({ ...tx, [key]: e.target.value })}
          />
        </label>
      ))}
      <p className="muted">
        A hash alone is accepted and saved with resolution pending. Example: 1
        USDT = 1000000 base units, decimals 6.
      </p>
    </div>
  );
}
function parseTx(tx: Record<string, string>): Tx {
  return transactionSchema.parse(
    Object.fromEntries(
      Object.entries(tx)
        .filter(([, v]) => v !== "")
        .map(([k, v]) => [k, k === "decimals" ? Number(v) : v]),
    ),
  );
}
function NewCaseDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: (c: Case) => void;
}) {
  const [tx, setTx] = useState<Record<string, string>>({ chain: "ethereum" }),
    [title, setTitle] = useState("Blockchain complaint"),
    [ref, setRef] = useState(""),
    [images, setImages] = useState<Extraction[]>([]),
    [reviewed, setReviewed] = useState(false),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false),
    [uploading, setUploading] = useState(false);
  const editTx = (v: Record<string, string>) => {
    setTx(v);
    setReviewed(false);
  };
  return (
    <div className="overlay">
      <section
        className="dialog panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-case"
      >
        <div className="heading">
          <h2 id="new-case">New case</h2>
          <button
            className="secondary"
            disabled={busy || uploading}
            onClick={onClose}
          >
            Close
          </button>
        </div>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setError(undefined);
            setBusy(true);
            try {
              const transaction = parseTx(tx);
              z.string().trim().min(3).max(160).parse(title);
              if (ref)
                z.string()
                  .regex(/^[A-Za-z0-9_/-]{3,80}$/)
                  .parse(ref);
              const c = await api<Case>("/cases", {
                title,
                sahyog_ref: ref || null,
                transaction,
                attachment_ids: images.map((i) => i.attachment_id),
                extraction_reviewed: reviewed,
              });
              await queryClient.invalidateQueries({ queryKey: ["cases"] });
              onDone(c);
            } catch (e) {
              setError(
                e instanceof z.ZodError
                  ? new Error(e.issues.map((i) => i.message).join("; "))
                  : e,
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
          </label>
          <label>
            SAHYOG reference (optional)
            <input value={ref} onChange={(e) => setRef(e.target.value)} />
          </label>
          <TransactionFields tx={tx} setTx={editTx} />
          <section className="panel">
            <h3>Screenshot or QR image</h3>
            <label>
              Upload PNG/JPEG (5 MB maximum)
              <input
                type="file"
                accept="image/png,image/jpeg"
                disabled={uploading || images.length >= 10}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  setUploading(true);
                  setReviewed(false);
                  try {
                    if (file.size > 5 * 1024 * 1024)
                      throw new Error("Image must be no larger than 5 MB");
                    const form = new FormData();
                    form.append("file", file);
                    const result = await api<Extraction>(
                      "/cases/extract-from-image",
                      form,
                    );
                    setImages((prev) => [...prev, result]);
                    setError(undefined);
                  } catch (e) {
                    setError(e);
                  } finally {
                    setUploading(false);
                    e.target.value = "";
                  }
                }}
              />
            </label>
            {uploading && <p>Extracting image…</p>}
            {images.map((image) => (
              <div key={image.attachment_id}>
                <a
                  href={
                    "/api/v1/cases/attachments/" +
                    image.attachment_id +
                    "/image"
                  }
                  target="_blank"
                  rel="noreferrer"
                >
                  Review uploaded image
                </a>
                {image.notices.map((n) => (
                  <p key={n}>{n}</p>
                ))}
                {image.candidates.length === 0 && (
                  <p>No candidates found. Enter the fields manually.</p>
                )}
                {image.candidates.map((c, i) => (
                  <div className="candidate" key={i}>
                    <code>{c.value}</code>
                    <span>
                      {c.type} · {Math.round(c.confidence * 100)}% ·{" "}
                      {c.checksum_ok === true
                        ? "✓ checksum"
                        : c.checksum_ok === false
                          ? "✕ checksum"
                          : "checksum unavailable"}{" "}
                      · {c.source}
                    </span>
                    {c.type === "tx_hash" ? (
                      <button
                        type="button"
                        onClick={() => editTx({ ...tx, tx_hash: c.value })}
                      >
                        Use hash
                      </button>
                    ) : c.type.endsWith("address") ? (
                      <>
                        <button
                          type="button"
                          onClick={() =>
                            editTx({ ...tx, victim_address: c.value })
                          }
                        >
                          Use victim
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            editTx({ ...tx, suspect_address: c.value })
                          }
                        >
                          Use suspect
                        </button>
                      </>
                    ) : (
                      <span>
                        Convert displayed amount to base units before entering.
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {images.length > 0 && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={reviewed}
                  onChange={(e) => setReviewed(e.target.checked)}
                />
                I reviewed the images and confirmed the editable fields above.
              </label>
            )}
          </section>
          <ErrorText error={error} />
          <button
            disabled={busy || uploading || (images.length > 0 && !reviewed)}
          >
            {busy ? "Creating…" : "Create case"}
          </button>
        </form>
      </section>
    </div>
  );
}
function CaseDetail({ user }: { user: User }) {
  const { id } = useParams();
  const c = useQuery({
    queryKey: ["case", id],
    queryFn: () => api<Case>("/cases/" + id),
  });
  const [add, setAdd] = useState(false),
    [tx, setTx] = useState<Record<string, string>>({ chain: "ethereum" }),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false);
  if (c.isPending) return <p>Loading case…</p>;
  if (!c.data) return <ErrorText error={c.error} />;
  const item = c.data;
  return (
    <>
      <Link to="/cases">← All cases</Link>
      <section className="panel">
        <h1>{item.title}</h1>
        <p>
          {item.case_ref} · {item.status}
        </p>
        <p>
          SAHYOG: {item.sahyog_ref || "No reference"} ·{" "}
          {new Date(item.created_at).toLocaleString()}
        </p>
        <SimulatedBadge source={item.source} />
      </section>
      <CaseWorkspace
        caseId={item.id}
        scenarioId={item.scenario_id}
        user={user}
      />
      <section className="panel">
        <h2>Victim transactions</h2>
        {item.transactions.map((t, i) => (
          <article key={i}>
            <h3>
              {t.chain} · {t.status}
            </h3>
            <dl>
              {Object.entries(t)
                .filter(([k]) => k !== "id")
                .map(([k, v]) => (
                  <React.Fragment key={k}>
                    <dt>{k.replaceAll("_", " ")}</dt>
                    <dd>{v ?? "Not reported"}</dd>
                  </React.Fragment>
                ))}
            </dl>
          </article>
        ))}
        {user.role === "INVESTIGATOR" && item.status !== "CLOSED" && (
          <button onClick={() => setAdd(!add)}>Add victim transaction</button>
        )}
        {add && (
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                await api("/cases/" + id + "/victim-transactions", parseTx(tx));
                await c.refetch();
                setAdd(false);
                setError(undefined);
              } catch (e) {
                setError(
                  e instanceof z.ZodError
                    ? new Error(e.issues.map((i) => i.message).join("; "))
                    : e,
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            <TransactionFields tx={tx} setTx={setTx} />
            <ErrorText error={error} />
            <button disabled={busy}>Save transaction</button>
          </form>
        )}
      </section>
      {item.attachments.length > 0 && (
        <section className="panel">
          <h2>Reviewed attachments</h2>
          {item.attachments.map((a) => (
            <p key={a.id}>
              <a
                href={"/api/v1/cases/attachments/" + a.id + "/image"}
                target="_blank"
                rel="noreferrer"
              >
                Open image
              </a>
              <br />
              <code>SHA-256: {a.sha256}</code>
            </p>
          ))}
        </section>
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LanguageProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/verify/:hash" element={<VerificationPage />} />
            <Route path="/*" element={<AppShell />} />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </LanguageProvider>
  </React.StrictMode>,
);
