import { test, expect } from "@playwright/test";
async function login(page, role = "investigator") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(role + "@sahyog.demo");
  await page.getByLabel("Password", { exact: true }).fill("E2e-password-2026");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Cases", exact: true }),
  ).toBeVisible();
}
test("hard scenario traces to a hub, attributes, confirms in both privacy modes and recommends routing", async ({
  page,
}) => {
  test.setTimeout(90000);
  await login(page);
  await page.getByRole("link", { name: "Scenarios", exact: true }).click();
  const seed = (Date.now() % 1000000) * 3;
  await page.getByLabel("Seed", { exact: true }).fill(String(seed));
  await page.getByLabel("Noise transfers").fill("0");
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/dev/scenarios") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Generate scenario", exact: true })
    .click();
  const scenario = await (await created).json();
  const row = page.locator(".scenario-row").filter({ hasText: scenario.id });
  await row.getByRole("button", { name: "Load as case" }).click();
  await expect(
    page.getByRole("heading", { name: "Investigation", exact: true }),
  ).toBeVisible();
  const caseId = page.url().split("/").pop();
  await page.getByRole("button", { name: "Run trace", exact: true }).click();
  await expect
    .poll(
      async () => {
        const r = await page.request.get("/api/v1/cases/" + caseId + "/graph");
        return r.ok() ? (await r.json()).status : "missing";
      },
      { timeout: 30000 },
    )
    .toBe("COMPLETED");
  await page
    .getByRole("button", { name: "2. Attribution", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Run attribution", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Run attribution", exact: true })
    .click();
  await expect(page.locator(".confidence strong")).toHaveText("90%");
  await expect(
    page.getByRole("heading", { name: "Demo Bharat Exchange", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Ask VASPs", exact: true }).click();
  await expect(page.locator(".confidence strong")).toHaveText("95%", {
    timeout: 30000,
  });
  await expect
    .poll(
      async () => {
        const r = await page.request.get(
          "/api/v1/cases/" + caseId + "/federated-lookup",
        );
        return (await r.json()).status;
      },
      { timeout: 30000 },
    )
    .toBe("COMPLETED");
  await expect(page.locator(".reply.yes")).toContainText(
    "Demo Bharat Exchange",
  );
  await expect(page.locator(".reply.timeout")).toContainText(
    "Demo Silent Exchange",
  );
  await page.getByLabel("Privacy mode").selectOption("psi");
  await page.getByRole("button", { name: "Ask VASPs", exact: true }).click();
  await expect
    .poll(
      async () => {
        const r = await page.request.get(
          "/api/v1/cases/" + caseId + "/federated-lookup",
        );
        const data = await r.json();
        return data.mode === "psi" ? data.status : "waiting";
      },
      { timeout: 30000 },
    )
    .toBe("COMPLETED");
  await page.getByRole("button", { name: "6. Routing", exact: true }).click();
  await page
    .getByRole("button", { name: "Recommend channel", exact: true })
    .click();
  await expect(page.getByText("DIRECT PORTAL", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Demo Bharat Exchange · issuer: TETHER", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "9. Fence", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Wallet fence", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "ADD", exact: true }).first().click();
  await page
    .getByRole("button", { name: "Replay synthetic events", exact: true })
    .click();
  await expect(
    page
      .locator("article strong")
      .filter({ hasText: /^FENCE_CROSS$/ })
      .first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "10. Dormancy", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Sleeper activation monitoring",
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/intelligence-workspace.png",
    fullPage: true,
  });
});
test("admin can run simulated probes and change replay mode", async ({
  page,
}) => {
  await login(page, "admin");
  await page.getByRole("link", { name: "Probe map", exact: true }).click();
  const exchanges = await (
    await page.request.get("/api/v1/probes/exchanges")
  ).json();
  const exchange = exchanges.find((e) => e.name === "Demo Bharat Exchange");
  await page
    .getByRole("combobox", { name: "Exchange", exact: true })
    .selectOption(exchange.id);
  const details = await (
    await page.request.get(`/api/v1/probes/exchanges/${exchange.id}/hotwallets`)
  ).json();
  const count = details.clusters.find(
    (c) => c.chain === "tron",
  ).matching_sweeps;
  await page.getByLabel("Probe count").fill("2");
  await page.getByRole("button", { name: "Run simulated probe" }).click();
  await expect(
    page.getByText(`tron · ${count + 2} matching sweeps`, { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Chain lookup", exact: true }).click();
  await page
    .getByRole("button", { name: "Enable live requests", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Enable replay mode", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Enable replay mode", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Enable live requests", exact: true }),
  ).toBeVisible();
});
