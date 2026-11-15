import { test, expect } from "@playwright/test";
import path from "node:path";
import { readFile } from "node:fs/promises";

test("离线外网约束下完整主流程、分享、故事、展示与撤权", async ({
  page,
  context,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname))
      return route.abort();
    return route.continue();
  });
  await page.goto("/setup");
  await page.getByLabel("你的称呼").fill("测试家人");
  await page.getByLabel("家庭名称").fill("合成记忆之家");
  await page.getByLabel("用户名", { exact: true }).fill("e2eowner");
  await page
    .getByLabel("口令", { exact: true })
    .fill("e2e-only-passphrase-2026");
  await page.getByRole("button", { name: "创建家庭相册" }).click();
  await expect(
    page.getByRole("heading", { name: "让记忆，常常相见。" }),
  ).toBeVisible();
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const headers = { "X-CSRF-Token": me.csrf };
  await page.request.patch("/api/v1/settings", {
    headers,
    data: {
      embedding_enabled: false,
      vlm_enabled: false,
      quiet_start: "00:00",
      quiet_end: "00:00",
    },
  });
  await page.getByRole("link", { name: "上传照片", exact: true }).click();
  await expect(page.getByLabel("加入家庭共享相册")).not.toBeChecked();
  await page.getByLabel("加入家庭共享相册").check();
  await page
    .locator("input[type=file]")
    .setInputFiles(
      path.resolve("../../runtime/e2e-samples/synthetic-0002.jpg"),
    );
  await expect(page.getByText(/上传成功/)).toBeVisible();
  const photos = await (await page.request.get("/api/v1/photos")).json();
  const pid = photos.items[0].id;
  await page.goto("/photos/" + pid);
  await expect(
    page.getByRole("heading", { name: "生活里的一个瞬间" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "编辑照片" }).click();
  await page.getByLabel("标题", { exact: true }).fill("合成旅行的日常");
  await page
    .getByLabel("人工说明", { exact: true })
    .fill("海边的合影，这是合成测试图片");
  await page.getByRole("button", { name: "保存人工校正" }).click();
  await expect(
    page.getByRole("heading", { name: "合成旅行的日常", level: 1 }),
  ).toBeVisible();
  await page.goto("/search");
  await page.getByRole("textbox", { name: "搜索家庭记忆" }).fill("海边");
  await page.getByRole("button", { name: "寻找记忆" }).click();
  await expect(
    page.getByRole("heading", { name: "找到 1 个候选瞬间" }),
  ).toBeVisible();
  await page.request.patch("/api/v1/settings", {
    headers,
    data: {
      embedding_enabled: false,
      vlm_enabled: false,
      quiet_start: "00:00",
      quiet_end: "00:00",
    },
  });
  // Real local metadata/quality processing, no model fake in this browser acceptance flow.
  const clustering = await page.request.post("/api/v1/events/recluster", {
    headers,
  });
  expect(clustering.ok()).toBeTruthy();
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/v1/events")).json()).items.length,
      { timeout: 20000 },
    )
    .toBeGreaterThan(0);
  const eventList = await (await page.request.get("/api/v1/events")).json();
  await page.goto("/events/" + eventList.items[0].id);
  await page.getByRole("button", { name: "生成故事草稿" }).click();
  await expect(
    page.getByRole("heading", { name: "编排一段家的故事" }),
  ).toBeVisible();
  await page.getByLabel("故事标题", { exact: true }).fill("我们的合成测试故事");
  await page.getByLabel("停留秒数", { exact: true }).fill("1");
  await page.getByRole("button", { name: "保存故事", exact: true }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "故事播放" })).toBeVisible();
  await page.getByRole("button", { name: "退出故事" }).click();
  await page.getByRole("button", { name: "生成 MP4", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible({
    timeout: 20000,
  });
  await page.request.post("/api/v1/playlists/regenerate", { headers });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "我们的家庭" })).toBeVisible();
  await page.getByRole("button", { name: "生成展示设备配对码" }).click();
  await page.getByRole("link", { name: "在此浏览器配对" }).click();
  await expect(page.getByRole("button", { name: "暂停轮播" })).toBeVisible();
  await page.getByRole("button", { name: "暂停轮播" }).click();
  await expect(page.getByRole("button", { name: "开始轮播" })).toBeVisible();
  await page.getByRole("button", { name: "下一张" }).click();
  await page.getByRole("button", { name: "收藏当前照片" }).click();
  await page.screenshot({
    path: "../../runtime/display-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.request.patch("/api/v1/photos/" + pid, {
    headers,
    data: { visibility: "private" },
  });
  await expect(page.getByText("好的记忆，值得等待。")).toBeVisible();
  await page.goto("/photos");
  await expect(
    page.locator(".image-badge").filter({ hasText: "仅自己" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/upload");
  await expect(page.getByText("把喜欢的日常，放进来")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../runtime/upload-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("空相框增量恢复、静默时段和断连保护", async ({ page, context }) => {
  const response = await page.request.post("/api/v1/auth/register", {
    data: {
      username: "display-regression",
      password: "regression-local-passphrase",
      household_name: "展示回归",
      display_name: "合成测试",
    },
  });
  expect(response.ok()).toBeTruthy();
  const { csrf } = await response.json();
  const headers = { "X-CSRF-Token": csrf };
  await page.request.patch("/api/v1/settings", {
    headers,
    data: {
      embedding_enabled: false,
      vlm_enabled: false,
      quiet_start: "00:00",
      quiet_end: "00:00",
    },
  });
  const device = await (
    await page.request.post("/api/v1/display/devices", {
      headers,
      data: { name: "regression" },
    })
  ).json();
  await page.request.post("/api/v1/display/pair", {
    data: { code: device.code },
  });
  await page.goto("/display");
  await expect(page.getByText("好的记忆，值得等待。")).toBeVisible();
  await page.request.post("/api/v1/photos/upload", {
    headers,
    multipart: {
      shared: "true",
      file: {
        name: "synthetic.jpg",
        mimeType: "image/jpeg",
        buffer: await readFile(
          path.resolve("../../runtime/e2e-samples/synthetic-0003.jpg"),
        ),
      },
    },
  });
  await page.request.post("/api/v1/playlists/regenerate", { headers });
  await expect(page.getByRole("button", { name: "暂停轮播" })).toBeVisible({
    timeout: 10000,
  });
  await context.route("**/api/v1/display/feed", (route) => route.abort());
  await expect(page.getByText("与家里的服务暂时断开")).toBeVisible({
    timeout: 10000,
  });
  await expect(page.locator(".display-photo img")).toHaveCount(0);
  await context.unroute("**/api/v1/display/feed");
  await expect(page.getByRole("button", { name: "暂停轮播" })).toBeVisible({
    timeout: 10000,
  });
  await page.request.patch("/api/v1/settings", {
    headers,
    data: {
      quiet_start: new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Shanghai",
        hour: "2-digit",
        minute: "2-digit",
      }).format(Date.now() - 120000),
      quiet_end: new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Shanghai",
        hour: "2-digit",
        minute: "2-digit",
      }).format(Date.now() + 120000),
    },
  });
  await expect(page.getByText("晚安，明天再见。")).toBeVisible({
    timeout: 10000,
  });
});

test("自动相册入口、日期集合、空态及人工覆盖", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const account = await page.request.post("/api/v1/auth/register", {
    data: {
      username: "albums-regression",
      password: "albums-local-passphrase",
      household_name: "相册回归",
      display_name: "合成测试",
    },
  });
  expect(account.ok()).toBeTruthy();
  const headers = { "X-CSRF-Token": (await account.json()).csrf };
  await page.request.patch("/api/v1/settings", {
    headers,
    data: { embedding_enabled: false, vlm_enabled: false },
  });
  for (const number of [2, 3]) {
    const response = await page.request.post("/api/v1/photos/upload", {
      headers,
      multipart: {
        shared: "true",
        file: {
          name: "synthetic.jpg",
          mimeType: "image/jpeg",
          buffer: await readFile(
            path.resolve(
              `../../runtime/e2e-samples/synthetic-000${number}.jpg`,
            ),
          ),
        },
      },
    });
    expect(response.ok()).toBeTruthy();
  }
  const photos = (await (await page.request.get("/api/v1/photos")).json())
    .items;
  const chosen = photos[0].id;
  await page.request.patch("/api/v1/photos/" + chosen, {
    headers,
    data: { taken_at: "2024-11-15T03:00:00Z", title: "同日的合成照片" },
  });
  await page.request.patch("/api/v1/photos/" + photos[1].id, {
    headers,
    data: { taken_at: "2024-06-01T03:00:00Z", title: "其他日期的合成照片" },
  });
  await page.goto("/");
  await page.getByRole("link", { name: /往年今日/ }).click();
  await expect(page).toHaveURL(/\/albums\/on_this_day/);
  await page.getByLabel("回忆日期").fill("2026-11-15");
  await expect(page.locator(".photo-card")).toHaveCount(1);
  await expect(page.getByText("同日的合成照片", { exact: true })).toBeVisible();
  await expect(
    page.getByText("其他日期的合成照片", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "慢慢长大", exact: true }).click();
  await expect(page.locator(".photo-card")).toHaveCount(0);
  await expect(page.getByText(/还没有足够的成长线索/)).toBeVisible();
  await page.getByRole("link", { name: "一起走过", exact: true }).click();
  await expect(page.getByText(/还没有发现旅行或出游线索/)).toBeVisible();

  // These are explicit human choices over real API data; no fake inference here.
  const person = await (
    await page.request.post("/api/v1/people", {
      headers,
      data: {
        name: "合成家人",
        manually_confirmed: true,
        is_family: true,
        growth_mode: "include",
      },
    })
  ).json();
  await page.request.post(`/api/v1/people/${person.id}/photos/${chosen}`, {
    headers,
  });
  await page.goto("/albums/growth");
  await expect(page.locator(".photo-card")).toHaveCount(1);
  await expect(
    page.locator(".photo-card").getByText("人工指定的成长人物：合成家人"),
  ).toBeVisible();
  await page.goto("/people");
  await page.getByRole("button", { name: "命名与纠错" }).click();
  await page.getByLabel("成长相册整理").selectOption("exclude");
  await page.getByRole("button", { name: "保存人物", exact: true }).click();
  await page.goto("/albums/growth");
  await expect(page.getByText(/还没有足够的成长线索/)).toBeVisible();
  await expect(page.locator(".photo-card")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});

test("本人私密照片自动整理、日期含同日和邻近日、批量共享确认", async ({
  page,
}) => {
  const registration = await page.request.post("/api/v1/auth/register", {
    data: {
      username: "personal-albums",
      password: "personal-albums-test-password",
      household_name: "个人整理测试",
      display_name: "合成家人",
    },
  });
  expect(registration.ok()).toBeTruthy();
  const headers = { "X-CSRF-Token": (await registration.json()).csrf };
  await page.request.patch("/api/v1/settings", {
    headers,
    data: { embedding_enabled: false, vlm_enabled: false },
  });
  const ids: string[] = [];
  for (const number of [2, 3]) {
    const response = await page.request.post("/api/v1/photos/upload", {
      headers,
      multipart: {
        shared: "false",
        file: {
          name: "synthetic.jpg",
          mimeType: "image/jpeg",
          buffer: await readFile(
            path.resolve(
              `../../runtime/e2e-samples/synthetic-000${number}.jpg`,
            ),
          ),
        },
      },
    });
    ids.push((await response.json()).photo_id);
  }
  for (const [index, id] of ids.entries())
    await page.request.patch("/api/v1/photos/" + id, {
      headers,
      data: {
        title: index === 0 ? "合成同日照片" : "合成邻近照片",
        taken_at: `2023-11-${index === 0 ? "15" : "18"}T03:00:00Z`,
      },
    });
  await page.goto("/albums/on_this_day");
  await page.getByLabel("回忆日期").fill("2026-11-15");
  await expect(page.locator(".photo-card")).toHaveCount(2);
  await expect(page.getByText(/已参与本人的自动整理/)).toBeVisible();
  expect(
    (
      await (
        await page.request.get(
          "/api/v1/albums/on_this_day?day=2026-11-15&scope=family",
        )
      ).json()
    ).total,
  ).toBe(0);
  await page.goto("/photos");
  await page.getByRole("button", { name: "选中本页我的照片" }).click();
  await expect(page.getByText("已选 2 张")).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "加入家庭共享" }).click();
  expect(
    (await (await page.request.get("/api/v1/photos/" + ids[0])).json())
      .visibility,
  ).toBe("private");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "加入家庭共享" }).click();
  await expect(page.getByText("已更新所选照片")).toBeVisible();
  await expect(
    page.locator(".image-badge").filter({ hasText: "仅自己" }),
  ).toHaveCount(0);
  expect(
    (
      await (
        await page.request.get(
          "/api/v1/albums/on_this_day?day=2026-11-15&scope=family",
        )
      ).json()
    ).total,
  ).toBe(2);
  await page.goto("/upload");
  await expect(page.getByLabel("加入家庭共享相册")).not.toBeChecked();
});

// Uses the Playwright server's temporary database, never the live family on port 8000.
test("永久删除家庭：确认、会话失效、原账号登录及创建新家庭", async ({
  page,
}) => {
  const familyName = "删除回归合成之家";
  const registration = await page.request.post("/api/v1/auth/register", {
    data: {
      username: "household-delete-regression",
      password: "delete-test-only-passphrase",
      household_name: familyName,
      display_name: "合成删除测试",
    },
  });
  expect(registration.ok()).toBeTruthy();
  const { csrf, household } = await registration.json();
  const originalUser = (
    await (await page.request.get("/api/v1/auth/me")).json()
  ).user;
  const headers = { "X-CSRF-Token": csrf };
  await page.request.patch("/api/v1/settings", {
    headers,
    data: { embedding_enabled: false, vlm_enabled: false, ocr_enabled: false },
  });
  expect(
    (await page.request.post("/api/v1/jobs/pause", { headers })).ok(),
  ).toBeTruthy();
  const upload = await page.request.post("/api/v1/photos/upload", {
    headers,
    multipart: {
      shared: "false",
      file: {
        name: "synthetic-delete.jpg",
        mimeType: "image/jpeg",
        buffer: await readFile(
          path.resolve("../../runtime/e2e-samples/synthetic-0001.jpg"),
        ),
      },
    },
  });
  expect(upload.ok()).toBeTruthy();
  await page.goto("/settings");
  const button = page.getByRole("button", { name: "永久删除此家庭" });
  let deleteRequests = 0;
  page.on("request", (request) => {
    if (request.method() === "DELETE") deleteRequests += 1;
  });
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    expect(dialog.message()).toContain(familyName);
    await dialog.dismiss();
  });
  await button.click();
  expect(deleteRequests).toBe(0);
  page.once("dialog", (dialog) => dialog.accept("不正确的名称"));
  await button.click();
  expect(deleteRequests).toBe(0);
  expect(
    (await page.request.get(`/api/v1/households/${household.id}`)).ok(),
  ).toBeTruthy();

  page.once("dialog", (dialog) => dialog.accept(familyName));
  const deletion = page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      response.url().includes(`/households/${household.id}`),
  );
  await button.click();
  expect((await deletion).status()).toBe(200);
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  expect(deleteRequests).toBe(1);
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
  await page
    .getByLabel("用户名", { exact: true })
    .fill("household-delete-regression");
  await page
    .getByLabel("口令", { exact: true })
    .fill("delete-test-only-passphrase");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/households\/new$/);
  await expect(
    page.getByRole("heading", { name: "为账号创建一个新家庭" }),
  ).toBeVisible();
  expect((await page.request.get("/api/v1/photos")).status()).toBe(403);
  await page.goto("/photos");
  await expect(page).toHaveURL(/\/households\/new$/);
  await page.getByLabel("家庭名称").fill("原账号的新测试家庭");
  await page.getByRole("button", { name: "创建新家庭", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "让记忆，常常相见。" }),
  ).toBeVisible();
  const recovered = await (await page.request.get("/api/v1/auth/me")).json();
  expect(recovered.user.id).toBe(originalUser.id);
  expect(recovered.role).toBe("owner");
  expect(recovered.household.id).not.toBe(household.id);
  expect(recovered.household.name).toBe("原账号的新测试家庭");
  expect((await (await page.request.get("/api/v1/photos")).json()).total).toBe(
    0,
  );
});
