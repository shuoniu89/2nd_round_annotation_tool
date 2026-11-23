import { expect, test } from "@playwright/test";

const password = "1";
const email = `browser_rater_${Date.now()}@example.edu`;

test("account creation, blind coding, autosave, verification, resume, isolation, and XLSX", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", error => browserErrors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByLabel("Username", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: "../test-results/login-desktop.png", fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create your account" }).click();
  await expect(page.getByRole("navigation", { name: "Post navigation", exact: true })).toBeVisible();
  const project = await (await page.request.get("/api/project/")).json();
  const record = project.records[0];
  const code = project.codes[0];
  const initial = await (await page.request.get(`/api/records/${record.id}/`)).json();
  expect(initial.record.ai_codes).toBeNull();
  await page.getByRole("checkbox", { name: code.header, exact: true }).check();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  // A burst of actions exercises the serialized autosave queue.
  await page.getByRole("checkbox", { name: project.codes[1].header, exact: true }).check();
  await page.getByRole("checkbox", { name: project.codes[2].header, exact: true }).check();
  await page.getByRole("checkbox", { name: project.codes[1].header, exact: true }).uncheck();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  await page.getByRole("button", { name: "Research notes" }).click();
  await page.getByLabel("Research notes", { exact: true }).fill("A note saved to this rater account.");
  await expect(page.getByRole("status")).toContainText("All changes saved");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "../test-results/workspace-desktop.png", fullPage: true, animations: "disabled" });
  await page.reload();
  await expect(page.getByRole("checkbox", { name: code.header, exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: project.codes[1].header, exact: true })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: project.codes[2].header, exact: true })).toBeChecked();
  await expect(page.getByLabel("Research notes", { exact: true })).toHaveValue("A note saved to this rater account.");
  await page.getByRole("button", { name: "Confirm codes & reveal AI" }).click();
  await expect(page.getByRole("heading", { name: "2. Verify AI annotations", exact: true })).toBeVisible();
  const revealed = await (await page.request.get(`/api/records/${record.id}/`)).json();
  expect(revealed.record.ai_codes.length).toBeGreaterThan(0);
  const complete = page.getByRole("button", { name: "Complete verification", exact: true });
  await expect(complete).toBeDisabled();
  for (const [index, key] of revealed.record.ai_codes.entries()) {
    const label = project.codes.find((c: { key: string }) => c.key === key).label;
    await page.getByRole("button", { name: `${index === 0 ? "Reject" : "Accept"} ${label}`, exact: true }).click();
  }
  await expect(page.getByRole("status")).toContainText("All changes saved");
  await expect(complete).toBeEnabled();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "../test-results/verification-desktop.png", fullPage: true, animations: "disabled" });
  await complete.click();
  await expect(page.getByRole("heading", { name: "Verification complete", exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  const completed = await (await page.request.get(`/api/records/${record.id}/`)).json();
  expect(completed.annotation.stage).toBe("complete");
  expect(completed.annotation.selected_codes).toEqual([project.codes[0].key, project.codes[2].key]);
  expect(completed.annotation.ai_reviews[revealed.record.ai_codes[0]]).toBe("rejected");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download my annotations" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("my_annotations.xlsx");
  await file.saveAs("../test-results/browser-export.xlsx");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await page.getByLabel("Email address").fill(email.toUpperCase());
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Post navigation", exact: true })).toBeVisible();
  // A new session resumes at the next unfinished post.
  const nextId = project.records[1].id;
  await expect(page.getByRole("checkbox", { name: code.header, exact: true })).toBeVisible();
  expect((await (await page.request.get(`/api/records/${nextId}/`)).json()).annotation.revision).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByLabel("Post", { exact: true })).toBeVisible();
  await page.screenshot({ path: "../test-results/workspace-mobile.png", fullPage: true, animations: "disabled" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Previous post", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Verification complete", exact: true })).toBeVisible();
  await page.getByLabel("Post", { exact: true }).selectOption(String(nextId));
  await expect(page.getByRole("heading", { name: "1. Select your codes", exact: true })).toBeVisible();
  expect(browserErrors).toEqual([]);
});

test("separate account starts without another rater’s annotations", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  const another = `isolated_rater_${Date.now()}`;
  await page.getByLabel("Email address").fill(`${another}@example.edu`);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create your account" }).click();
  await expect(page.getByRole("navigation", { name: "Post navigation", exact: true })).toBeVisible();
  const project = await (await page.request.get("/api/project/")).json();
  expect(project.records.every((record: { stage: string }) => record.stage === "not_started")).toBe(true);
  const detail = await (await page.request.get(`/api/records/${project.records[0].id}/`)).json();
  expect(detail.annotation.selected_codes).toEqual([]);
  expect(detail.annotation.notes).toBe("");
  expect(detail.record.ai_codes).toBeNull();
  // Simulate a saved request whose response is lost in transit, then retry it.
  await page.route("**/api/records/*/annotation", async route => {
    await route.fetch();
    await route.abort("failed");
  }, { times: 1 });
  await page.getByRole("checkbox", { name: project.codes[0].header, exact: true }).check();
  await expect(page.getByRole("button", { name: "Retry saving" })).toBeVisible();
  const afterLostResponse = await (await page.request.get(`/api/records/${project.records[0].id}/`)).json();
  expect(afterLostResponse.annotation.revision).toBe(1);
  await page.getByRole("button", { name: "Retry saving" }).click();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  const afterRetry = await (await page.request.get(`/api/records/${project.records[0].id}/`)).json();
  expect(afterRetry.annotation.revision).toBe(1);
  expect(afterRetry.annotation.selected_codes).toEqual([project.codes[0].key]);
});

