import { test, expect } from "@playwright/test";
const screenshotDir =
  process.env.SG_E2E_ARTIFACT_DIR ?? "../artifacts/shopguide/m5";
async function choose(page: any, model = "TEST-DS") {
  await page.goto("/");
  await page.getByTestId("product-" + model).click();
  await expect(page.getByTestId("product-" + model)).toHaveAttribute(
    "aria-pressed",
    "true",
  );
}
async function ask(page: any, text = "怎样启动这个商品？") {
  await page.getByRole("textbox", { name: "输入问题" }).fill(text);
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(
    page.locator(".exchange").last().locator(".step-card"),
  ).toBeVisible();
}

test("desktop chat, real source dialog, follow-up, reload and feedback", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await choose(page);
  await ask(page);
  await expect(page.locator(".step-card").last()).toContainText("红色 POWER");
  await page
    .getByRole("button", { name: "查看来源", exact: false })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("img", { name: "说明书整页" })).toBeVisible();
  await page.getByRole("button", { name: "放大", exact: true }).click();
  await expect(page.locator(".source-scroll>div")).toHaveAttribute(
    "style",
    /150%/,
  );
  await page.screenshot({
    path: `${screenshotDir}/source-dialog.png`,
  });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "追问这一步" }).last().click();
  await expect(page.locator(".reply-tag")).toBeVisible();
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(page.locator(".step-card")).toHaveCount(2);
  await page.reload();
  await expect(page.locator(".step-card")).toHaveCount(2);
  await page.getByRole("button", { name: "反馈问题" }).last().click();
  await page.getByRole("combobox").last().selectOption("image");
  await page.getByRole("button", { name: "提交反馈", exact: true }).click();
  await expect(page.getByText("反馈已保存")).toBeVisible();
  await expect(page.getByTestId("product-TEST-DS")).toBeInViewport();
  await expect(page.locator(".brand")).toBeInViewport();
  await page.screenshot({ path: `${screenshotDir}/desktop.png` });
});

test("product switch, private upload, and confirmed simulated service", async ({
  page,
}) => {
  await choose(page);
  await ask(page);
  await page.getByTestId("product-TEST-DS2").click();
  await expect(page.getByTestId("product-TEST-DS2")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page
    .locator("input[type=file]")
    .setInputFiles("tests/fixtures/panel.png");
  await expect(page.locator(".attachments img")).toBeVisible();
  await ask(page, "请看看这张图，并说明如何连接？");
  await expect(page.locator(".step-card").last()).toContainText("CONNECT");
  await page.getByRole("button", { name: "模拟售后", exact: false }).click();
  await expect(page.getByRole("button", { name: "确认创建" })).toBeVisible();
  await page.getByRole("button", { name: "确认创建" }).click();
  await expect(page.getByText("模拟工单已创建")).toBeVisible();
  await page.reload();
  await expect(page.getByText("模拟工单已创建")).toBeVisible();
});

test("mobile layout and keyboard input stay usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByLabel("当前商品")).toBeEnabled();
  await page.getByLabel("当前商品").selectOption({ label: "TEST-DS" });
  await page.getByRole("textbox", { name: "输入问题" }).fill("怎样启动？");
  await page.getByRole("textbox", { name: "输入问题" }).press("Enter");
  await expect(page.locator(".step-card")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: `${screenshotDir}/mobile.png` });
  await page
    .getByRole("button", { name: "查看来源", exact: false })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});

test("clarification and escaped user text", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeVisible();
  await page
    .getByRole("textbox", { name: "输入问题" })
    .fill('<img src=x onerror="window.pwned=true">怎么启动？');
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(page.getByText("请先选择要咨询的商品和型号。")).toBeVisible();
  expect(await page.evaluate(() => (window as any).pwned)).toBeUndefined();
});

test("cancel running work, then continue without duplicating messages", async ({
  page,
}) => {
  await choose(page);
  await page.getByRole("textbox", { name: "输入问题" }).fill("请说明启动方式");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await page.getByRole("button", { name: "取消处理", exact: true }).click();
  await expect(
    page.getByText("已取消处理。已完成的模拟操作仍保留。"),
  ).toBeVisible();
  await expect(page.locator(".exchange")).toHaveCount(1);
  await ask(page, "请重新说明启动方式");
  await expect(page.locator(".exchange")).toHaveCount(2);
});

test("resume a running conversation after browser reload", async ({ page }) => {
  await choose(page);
  await page.getByRole("textbox", { name: "输入问题" }).fill("请说明按钮位置");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "取消处理", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.locator(".step-card")).toBeVisible();
  await expect(page.locator(".exchange")).toHaveCount(1);
});

test("200 percent text keeps controls and source access available", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1080 });
  await choose(page);
  await page.evaluate(() => (document.documentElement.style.fontSize = "32px"));
  await ask(page);
  await expect(
    page.getByRole("textbox", { name: "输入问题" }),
  ).toBeInViewport();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page
    .getByRole("button", { name: "查看来源", exact: false })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});

test("Enter during product transition waits for the committed session revision", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByLabel("当前商品")).toBeEnabled();
  await page.route("**/v1/sessions/*/product", async (route) => {
    const response = await route.fetch();
    await page.waitForTimeout(350);
    await route.fulfill({ response });
  });
  await page.getByLabel("当前商品").selectOption({ label: "TEST-DS" });
  await page.getByRole("textbox", { name: "输入问题" }).fill("怎样启动？");
  await page.getByRole("textbox", { name: "输入问题" }).press("Enter");
  await expect(page.locator(".step-card")).toBeVisible();
  await expect(page.locator(".exchange")).toHaveCount(1);
  await expect(page.getByText("会话已更新，请重试。")).not.toBeVisible();
});

test("failed product transition does not send the queued question to the old scope", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByLabel("当前商品")).toBeEnabled();
  let submitted = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/messages"))
      submitted++;
  });
  await page.route("**/v1/sessions/*/product", async (route) => {
    await page.waitForTimeout(350);
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: { code: "SESSION_REVISION_CONFLICT" } }),
    });
  });
  await page.getByLabel("当前商品").selectOption({ label: "TEST-DS" });
  await page.getByRole("textbox", { name: "输入问题" }).fill("怎样启动？");
  await page.getByRole("textbox", { name: "输入问题" }).press("Enter");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("当前商品")).toBeEnabled();
  expect(submitted).toBe(0);
  await expect(page.locator(".exchange")).toHaveCount(0);
});

test("query created simulated ticket without preparing another", async ({ page }) => {
  await choose(page);
  await page.getByRole("textbox", { name: "输入问题" }).fill("申请售后工单");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await page.getByRole("button", { name: "确认创建", exact: true }).click();
  await expect(page.getByText("模拟工单已创建", { exact: true })).toBeVisible();
  const ticket = await page.locator(".success small").innerText();
  await page.getByRole("button", { name: "查询此工单", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "输入问题" })).toHaveValue(`查询工单 ${ticket}`);
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  const latest = page.locator(".exchange").last();
  await expect(latest.getByText(ticket, { exact: true })).toBeVisible();
  await expect(latest.getByText("模拟工单已创建", { exact: true })).toBeVisible();
  await expect(latest.getByText("仅查询本地模拟工单的创建记录，未接入真实维修进度。")).toBeVisible();
  await expect(latest.getByRole("button", { name: "确认创建" })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".exchange").last().getByText(ticket, { exact: true })).toBeVisible();
});
