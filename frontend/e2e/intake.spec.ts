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
test("investigator reviews an image, creates a hash-only case, filters and adds a victim", async ({
  page,
}) => {
  await login(page);
  const screenshot = await page.screenshot();
  await page.getByRole("button", { name: "New case" }).click();
  const title = "Browser intake " + Date.now();
  await page.getByLabel("Title", { exact: true }).fill(title);
  await page
    .getByLabel("Transaction hash", { exact: true })
    .fill("a".repeat(64));
  await page
    .getByLabel("Upload PNG/JPEG")
    .setInputFiles({
      name: "complaint.png",
      mimeType: "image/png",
      buffer: screenshot,
    });
  await expect(
    page.getByRole("link", { name: "Review uploaded image" }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByRole("button", { name: "Create case", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("I reviewed the images").check();
  await page.getByRole("button", { name: "Create case", exact: true }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reviewed attachments" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add victim transaction" }).click();
  await page.getByRole("combobox", { name: "Chain", exact: true }).selectOption("tron");
  await page
    .getByLabel("Transaction hash", { exact: true })
    .fill("b".repeat(64));
  await page.getByRole("button", { name: "Save transaction" }).click();
  await expect(
    page.getByRole("heading", { name: "tron · PENDING_RESOLUTION" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "All cases" }).click();
  await page.getByLabel("search", { exact: true }).fill(title);
  await expect(page.getByRole("row").filter({ hasText: title })).toHaveCount(1);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
});
test("supervisor can view cases without investigator actions", async ({
  page,
}) => {
  await login(page, "supervisor");
  await expect(page.getByRole("button", { name: "New case" })).toHaveCount(0);
  await page
    .getByRole("button", { name: "Authenticator", exact: true })
    .click();
  await expect(page.getByLabel("Six-digit code")).toBeVisible();
});
test("mock complaint can be imported through the UI", async ({
  page,
  request,
}) => {
  const ref = "BROWSER-" + Date.now();
  const response = await request.post(
    "http://localhost:8001/sahyog/complaints",
    {
      headers: { Authorization: "Bearer " + process.env.SAHYOG_SERVICE_TOKEN },
      data: {
        ref,
        title: "Imported browser complaint",
        transaction: { chain: "tron", tx_hash: "c".repeat(64) },
      },
    },
  );
  expect(response.status()).toBe(201);
  await login(page);
  await page
    .getByLabel("SAHYOG complaint reference", { exact: true })
    .fill(ref);
  await page.getByRole("button", { name: "Import complaint" }).click();
  await expect(
    page.getByRole("heading", { name: "Imported browser complaint" }),
  ).toBeVisible();
  await expect(
    page.getByText("Simulated · simulated", { exact: true }),
  ).toBeVisible();
});
