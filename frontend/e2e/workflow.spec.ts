import { test, expect, Page } from "@playwright/test";
import { spawnSync } from "node:child_process";

async function login(page: Page, role = "investigator") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(role + "@sahyog.demo");
  await page.getByLabel("Password", { exact: true }).fill("E2e-password-2026");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Cases", exact: true }),
  ).toBeVisible();
}

async function post(page: Page, path: string, data = {}) {
  const csrf = await (await page.request.get("/api/v1/auth/csrf")).json();
  const response = await page.request.post("/api/v1" + path, {
    headers: { "X-CSRF-Token": csrf.csrf_token },
    data,
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("multi-victim workspace, second-key disclosure, sealed export and draft-only forecast", async ({
  page,
  browser,
}) => {
  test.setTimeout(120000);
  await login(page);
  const seed = Date.now() % 1000000;
  const scenario = await post(page, "/dev/scenarios", {
    preset: "multi-victim",
    seed,
    noise_level: 0,
  });
  const cases = await post(page, `/dev/scenarios/${scenario.id}/load-as-case`);
  const caseId = cases[0].id;
  await post(page, `/cases/${caseId}/trace`);
  await expect
    .poll(async () => {
      const response = await page.request.get(`/api/v1/cases/${caseId}/graph`);
      return response.ok() ? (await response.json()).status : "waiting";
    })
    .toBe("COMPLETED");
  await page.goto(`/cases/${caseId}`);
  await page.getByRole("button", { name: "3. Taint", exact: true }).click();
  await page.getByRole("button", { name: "Run Dye Pack", exact: true }).click();
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `/api/v1/cases/${caseId}/taint/summary`,
      );
      return response.ok()
        ? (await response.json()).traceable_amount
        : "waiting";
    })
    .toBe("14000000000");
  await expect(page.locator(".taint-pie")).toContainText("9000");
  await page.getByLabel("Time replay").focus();
  await page.getByLabel("Time replay").press("Home");
  await page.getByLabel("Time replay").press("End");
  await page.getByRole("button", { name: "4. Impact", exact: true }).click();
  await page
    .getByRole("button", { name: "Compute impact", exact: true })
    .click();
  await expect(page.locator(".impact.blocked")).toHaveText("BLOCKED");
  await expect(page.getByLabel("Freeze share")).toBeDisabled();
  await page.getByRole("button", { name: "5. Approval", exact: true }).click();
  await page
    .getByRole("button", { name: "Create request draft", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "DISCLOSURE · DRAFT", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "DISCLOSURE · DRAFT", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Request timeline", exact: true }),
  ).toBeVisible();
  const requestUrl = page.url();
  const reason =
    "Reviewed the shared victim path, swept funds and impact; request account disclosure for investigation.";
  await page.getByLabel("Written justification").fill(reason);
  await expect(page.getByLabel("Written justification")).toHaveValue(reason);
  await expect(
    page.getByRole("button", { name: "Propose request", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Propose request", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "DISCLOSURE · PROPOSED", exact: true }),
  ).toBeVisible();
  const supervisorContext = await browser.newContext();
  const supervisor = await supervisorContext.newPage();
  await login(supervisor, "supervisor");
  await supervisor.goto(requestUrl);
  const otp = spawnSync(
    "../.venv/bin/python",
    [
      "-c",
      "import pyotp,time; from sqlalchemy import select; from app.core.db import SessionLocal; from app.models.entities import User; db=SessionLocal(); user=db.scalar(select(User).where(User.email=='supervisor@sahyog.demo')); step=max(int(time.time())//30,(user.last_totp_step or 0)+1); print(pyotp.TOTP(user.totp_secret).at(step*30))",
    ],
    { cwd: "../backend", env: process.env, encoding: "utf8" },
  );
  expect(otp.status).toBe(0);
  await supervisor.getByLabel("Written justification").fill(reason);
  await supervisor
    .getByLabel("Approval authenticator code")
    .fill(otp.stdout.trim());
  await supervisor
    .getByRole("button", { name: "Approve with second key", exact: true })
    .click();
  await expect(
    supervisor.getByRole("heading", {
      name: "DISCLOSURE · ACKNOWLEDGED",
      exact: true,
    }),
  ).toBeVisible({ timeout: 15000 });
  await supervisorContext.close();
  await page.goto(`/cases/${caseId}`);
  await page.getByRole("button", { name: "7. Report", exact: true }).click();
  const built = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/cases/${caseId}/reports`) &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  const report = await (await built).json();
  await expect(
    page.getByText("Report version 1", { exact: true }),
  ).toBeVisible();
  const pdf = await page.request.get(`/api/v1/reports/${report.id}/pdf`);
  expect(pdf.ok()).toBeTruthy();
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
  const bundle = await (
    await page.request.get(`/api/v1/reports/${report.id}/bundle.json`)
  ).json();
  const item = report.items.find((item) => item.kind === "case");
  const proof = await (
    await page.request.get(`/api/v1/reports/${report.id}/proof/${item.id}`)
  ).json();
  const publicContext = await browser.newContext();
  const verification = await publicContext.newPage();
  await verification.goto(`/verify/${report.bundle_hash}`);
  await expect(
    verification.getByRole("heading", {
      name: "Verified, unchanged",
      exact: true,
    }),
  ).toBeVisible();
  await verification.getByLabel("Evidence bundle file").setInputFiles({
    name: "bundle.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(bundle)),
  });
  await expect(
    verification.getByText("Uploaded bundle matches seal", { exact: true }),
  ).toBeVisible();
  await verification.getByLabel("Evidence bundle file").setInputFiles({
    name: "changed.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ ...bundle, title: "changed" })),
  });
  await expect(
    verification.getByText("Uploaded bundle differs from seal", {
      exact: true,
    }),
  ).toBeVisible();
  await verification
    .getByLabel("Inclusion proof JSON")
    .fill(JSON.stringify(proof));
  await verification
    .getByRole("button", { name: "Verify item inclusion", exact: true })
    .click();
  await expect(
    verification.getByText("Item verified in bundle", { exact: true }),
  ).toBeVisible();
  await publicContext.close();
  for (let offset = 1; offset <= 8; offset++)
    await post(page, "/dev/scenarios", { seed: seed + offset, noise_level: 0 });
  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await login(admin, "admin");
  await post(admin, "/dev/forecast/train");
  const audit = await (await admin.request.get("/api/v1/audit/verify")).json();
  expect(audit.valid).toBeTruthy();
  await adminContext.close();
  await page.getByRole("button", { name: "8. Forecast", exact: true }).click();
  await page.getByRole("button", { name: "Run forecast", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Pre-stage draft only", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Pre-stage draft only", exact: true })
    .click();
  await expect
    .poll(async () => {
      const rows = await (
        await page.request.get(`/api/v1/cases/${caseId}/requests`)
      ).json();
      return rows.some(
        (row) => row.package.forecast_only && row.state === "DRAFT",
      );
    })
    .toBeTruthy();
  await page.getByLabel("Language", { exact: true }).selectOption("hi");
  await expect(
    page.getByRole("link", { name: "विश्लेषण", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Language", { exact: true }).selectOption("en");
  await page.screenshot({
    path: "test-results/tasks-8-13-workspace.png",
    fullPage: true,
  });
});
