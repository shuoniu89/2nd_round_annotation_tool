import { expect, test } from "@playwright/test";

const password = "1";
type Code = { key: string; header: string; label: string; kind: string };
const byKey = (codes: Code[], key: string) => codes.find(c => c.key === key)!;
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
  // E is a subtheme of theme AI; F and G are subthemes of theme AJ.
  const code = byKey(project.codes, "E");
  const theme = byKey(project.codes, "AI");
  const [sub1, sub2, theme2] = ["F", "G", "AJ"].map(key => byKey(project.codes, key));
  const initial = await (await page.request.get(`/api/records/${record.id}/`)).json();
  expect(initial.record.ai_codes).toBeNull();
  // Subthemes stay hidden until their theme is selected.
  await expect(page.getByRole("checkbox", { name: code.header, exact: true })).toHaveCount(0);
  await page.getByRole("checkbox", { name: theme.header, exact: true }).check();
  await page.getByRole("checkbox", { name: code.header, exact: true }).check();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  // A burst of actions exercises the serialized autosave queue.
  await page.getByRole("checkbox", { name: theme2.header, exact: true }).check();
  await page.getByRole("checkbox", { name: sub1.header, exact: true }).check();
  await page.getByRole("checkbox", { name: sub2.header, exact: true }).check();
  await page.getByRole("checkbox", { name: sub1.header, exact: true }).uncheck();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  await page.getByRole("button", { name: "Research notes" }).click();
  await page.getByLabel("Research notes", { exact: true }).fill("A note saved to this rater account.");
  await expect(page.getByRole("status")).toContainText("All changes saved");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "../test-results/workspace-desktop.png", fullPage: true, animations: "disabled" });
  await page.reload();
  await expect(page.getByRole("checkbox", { name: code.header, exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: sub1.header, exact: true })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: sub2.header, exact: true })).toBeChecked();
  await expect(page.getByLabel("Research notes", { exact: true })).toHaveValue("A note saved to this rater account.");
  await page.getByRole("button", { name: "Confirm codes & reveal AI" }).click();
  await expect(page.getByRole("heading", { name: "2. Verify AI annotations", exact: true })).toBeVisible();
  const revealed = await (await page.request.get(`/api/records/${record.id}/`)).json();
  expect(revealed.record.ai_codes.length).toBeGreaterThan(0);
  const complete = page.getByRole("button", { name: "Complete verification", exact: true });
  await expect(complete).toBeDisabled();
  // AI codes the rater already chose are agreed automatically and have no buttons.
  const mine = ["E", "G", "AI", "AJ"];
  // Only AI subthemes are verified; AI theme codes are not shown.
  const aiSubthemes = revealed.record.ai_codes.filter((key: string) => byKey(project.codes, key).kind === "detailed");
  const agreed = aiSubthemes.filter((key: string) => mine.includes(key));
  const toReview = aiSubthemes.filter((key: string) => !mine.includes(key));
  for (const key of revealed.record.ai_codes.filter((key: string) => byKey(project.codes, key).kind === "broad")) {
    await expect(page.getByRole("button", { name: `Accept ${byKey(project.codes, key).label}`, exact: true })).toHaveCount(0);
  }
  expect(toReview.length).toBeGreaterThan(0);
  for (const key of agreed) {
    expect(revealed.annotation.ai_reviews[key]).toBe("agreed");
    await expect(page.getByRole("button", { name: `Accept ${byKey(project.codes, key).label}`, exact: true })).toHaveCount(0);
  }
  for (const [index, key] of toReview.entries()) {
    await page.getByRole("button", { name: `${index === 0 ? "Reject" : "Accept"} ${byKey(project.codes, key).label}`, exact: true }).click();
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
  expect(completed.annotation.selected_codes).toEqual(["E", "G", "AI", "AJ"]);
  expect(completed.annotation.ai_reviews[toReview[0]]).toBe("rejected");
  for (const key of agreed) expect(completed.annotation.ai_reviews[key]).toBe("agreed");
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
  await expect(page.getByRole("checkbox", { name: theme.header, exact: true })).toBeVisible();
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
  const theme = byKey(project.codes, "AI");
  await page.getByRole("checkbox", { name: theme.header, exact: true }).check();
  await expect(page.getByRole("button", { name: "Retry saving" })).toBeVisible();
  const afterLostResponse = await (await page.request.get(`/api/records/${project.records[0].id}/`)).json();
  expect(afterLostResponse.annotation.revision).toBe(1);
  await page.getByRole("button", { name: "Retry saving" }).click();
  await expect(page.getByRole("status")).toContainText("All changes saved");
  const afterRetry = await (await page.request.get(`/api/records/${project.records[0].id}/`)).json();
  expect(afterRetry.annotation.revision).toBe(1);
  expect(afterRetry.annotation.selected_codes).toEqual(["AI"]);
});


test("cascading theme and subtheme selection", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await page.getByLabel("Email address").fill(`cascade_rater_${Date.now()}@example.edu`);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create your account" }).click();
  await expect(page.getByRole("navigation", { name: "Post navigation", exact: true })).toBeVisible();
  const project = await (await page.request.get("/api/project/")).json();
  const recordId = project.records[0].id;
  const saved = async () => {
    await expect(page.getByRole("status")).toContainText("All changes saved");
    return (await (await page.request.get(`/api/records/${recordId}/`)).json()).annotation.selected_codes;
  };
  const box = (key: string) => page.getByRole("checkbox", { name: byKey(project.codes, key).header, exact: true });
  // "Show subthemes" previews without selecting; picking a subtheme selects its theme too.
  await page.getByRole("button", { name: "Show subthemes" }).first().click();
  await box("J").check();
  await expect(box("AH")).toBeChecked();
  expect(await saved()).toEqual(["J", "AH"]);
  // "All" selects every subtheme of Relational Breakdown (AV) in one step.
  await box("AV").check();
  await page.getByRole("button", { name: "Select all subthemes of Relational Breakdown" }).click();
  expect(await saved()).toEqual(["J", "Y", "AA", "AC", "AD", "AH", "AV"]);
  await page.getByRole("button", { name: "Clear subthemes of Relational Breakdown" }).click();
  expect(await saved()).toEqual(["J", "AH", "AV"]);
  // Unchecking a theme clears its subthemes.
  await box("AH").uncheck();
  expect(await saved()).toEqual(["AV"]);
  // Search shows matching subthemes even under unselected themes.
  await page.getByRole("searchbox", { name: "Search codes" }).fill("memory");
  await box("T").check();
  expect(await saved()).toEqual(["T", "AN", "AV"]);
  // A selected theme without any subtheme triggers a reminder, inline and before confirming.
  await page.getByRole("searchbox", { name: "Search codes" }).fill("");
  const warning = page.locator(".warning-message");
  await expect(warning).toContainText("Relational Breakdown");
  await expect(warning).not.toContainText("Reliable Alliance");
  await expect(page.getByText("No subtheme selected.", { exact: false })).toHaveCount(1);
  let dialogText = "";
  page.once("dialog", dialog => { dialogText = dialog.message(); void dialog.dismiss(); });
  await page.getByRole("button", { name: "Confirm codes & reveal AI" }).click();
  expect(dialogText).toContain("Relational Breakdown");
  await expect(page.getByRole("heading", { name: "1. Select your codes", exact: true })).toBeVisible();
  page.once("dialog", dialog => void dialog.accept());
  await page.getByRole("button", { name: "Confirm codes & reveal AI" }).click();
  await expect(page.getByRole("heading", { name: "2. Verify AI annotations", exact: true })).toBeVisible();
  // Raters can go back and change their codes after the reveal.
  page.once("dialog", dialog => void dialog.accept());
  await page.getByRole("button", { name: "Edit my codes" }).click();
  await expect(page.getByRole("heading", { name: "1. Select your codes", exact: true })).toBeVisible();
  await expect(page.getByText("You are editing your codes after seeing the AI annotations")).toBeVisible();
  await box("AV").uncheck();
  expect(await saved()).toEqual(["T", "AN"]);
  await page.getByRole("button", { name: "Confirm codes & return to verification" }).click();
  await expect(page.getByRole("heading", { name: "2. Verify AI annotations", exact: true })).toBeVisible();
  // Reset deletes this rater's annotations after typing RESET.
  page.once("dialog", dialog => void dialog.accept("RESET"));
  await page.getByRole("button", { name: "Reset my annotations" }).click();
  await expect(page.getByRole("heading", { name: "1. Select your codes", exact: true })).toBeVisible();
  await expect(box("AN")).not.toBeChecked();
  const after = await (await page.request.get("/api/project/")).json();
  expect(after.records.every((r: { stage: string }) => r.stage === "not_started")).toBe(true);
});
