import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";

async function register(page: Page, username: string) {
  const response = await page.request.post("/api/v1/auth/register", {
    data: {
      username,
      password: "enhancements-test-passphrase",
      household_name: "合成增强回归",
      display_name: "合成家人",
    },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  const headers = { "X-CSRF-Token": body.csrf };
  const settings = await page.request.patch("/api/v1/settings", {
    headers,
    data: {
      embedding_enabled: false,
      vlm_enabled: false,
      faces_enabled: false,
      ocr_enabled: false,
    },
  });
  expect(settings.ok(), await settings.text()).toBeTruthy();
  expect(
    (await page.request.post("/api/v1/jobs/pause", { headers })).ok(),
  ).toBeTruthy();
  return { ...body, headers };
}

test("双层相册：私密影片跨分页、暂停保持、断连停止与共享撤回", async ({
  page,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { headers, household } = await register(
    page,
    "album-film-enhancements",
  );
  const source = await readFile(
    path.resolve("../../runtime/e2e-samples/synthetic-0002.jpg"),
  );
  const personResponse = await page.request.post("/api/v1/people", {
    headers,
    data: {
      name: "人工确认的合成人物",
      manually_confirmed: true,
      is_family: true,
      growth_mode: "include",
    },
  });
  expect(personResponse.ok()).toBeTruthy();
  const person = await personResponse.json();
  const ids: string[] = [];
  for (let index = 0; index < 43; index++) {
    // JPEG permits trailing data; this changes file identity without introducing real photos.
    // Assign the known test date immediately, keeping each fixture outside the near-duplicate time window.
    const upload = await page.request.post("/api/v1/photos/upload", {
      headers,
      multipart: {
        shared: "false",
        file: {
          name: `synthetic-${index}.jpg`,
          mimeType: "image/jpeg",
          buffer: Buffer.concat([
            source,
            Buffer.from(`synthetic-fixture-${index}`),
          ]),
        },
      },
    });
    expect(upload.ok()).toBeTruthy();
    const { photo_id: id } = await upload.json();
    ids.push(id);
    expect(
      (
        await page.request.patch(`/api/v1/photos/${id}`, {
          headers,
          data: {
            taken_at: `${1980 + index}-11-15T03:00:00+00:00`,
            title: `合成回忆 ${index}`,
          },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await page.request.post(`/api/v1/people/${person.id}/photos/${id}`, {
          headers,
        })
      ).ok(),
    ).toBeTruthy();
  }
  const movie = await (
    await page.request.get("/api/v1/albums/growth/playback?limit=200")
  ).json();
  expect(movie.source_total).toBe(43);
  expect(movie.selected_count).toBe(43);
  expect(movie.items.map((photo: { id: string }) => photo.id)).toEqual(ids);
  await page.goto("/albums/growth");
  const player = page.getByRole("region", { name: "慢慢长大回忆影片" });
  await expect(player.locator(".album-film-image")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "对应照片", exact: true }),
  ).toBeVisible();
  await expect(player.getByText(/本相册 43 张照片/)).toBeVisible();
  await expect(player.getByText(/仅自己可见/)).toBeVisible();
  await page.screenshot({
    path: "../../runtime/album-film-desktop.png",
    fullPage: false,
  });
  const viewport = page.viewportSize();
  await page.setViewportSize({ width: 390, height: 844 });
  await player.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../runtime/album-film-mobile.png",
    fullPage: false,
  });
  if (viewport) await page.setViewportSize(viewport);
  await player.getByRole("button", { name: "暂停相册影片" }).click();
  const refreshed = page.waitForResponse(
    (response) =>
      response.url().includes("/albums/growth/playback") &&
      response.request().method() === "GET",
  );
  await refreshed;
  await expect(
    player.getByRole("button", { name: "播放相册影片" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.locator(".pagination")).toContainText("2 / 2");
  await expect(player.getByText(/本相册 43 张照片/)).toBeVisible();
  await expect(
    player.getByRole("button", { name: "播放相册影片" }),
  ).toBeVisible();

  await page
    .context()
    .route("**/api/v1/albums/growth/playback**", (route) => route.abort());
  await expect(player.getByText(/无法验证照片权限/)).toBeVisible({
    timeout: 10000,
  });
  await expect(player.locator(".album-film-image")).toHaveCount(0);
  await page.context().unroute("**/api/v1/albums/growth/playback**");
  await expect(player.locator(".album-film-image")).toBeVisible({
    timeout: 10000,
  });
  await page.getByLabel("成长人物", { exact: true }).selectOption(person.id);
  await expect(
    page.getByRole("heading", { name: "同一人物的跨年对照" }),
  ).toBeVisible();
  await expect(page.getByAltText("1980 年的成长记录")).toBeVisible();
  await expect(page.getByAltText("2022 年的成长记录")).toBeVisible();
  const shared = ids[0];
  await page.request.patch(`/api/v1/photos/${shared}`, {
    headers,
    data: { visibility: "family" },
  });
  const invite = await (
    await page.request.post(`/api/v1/households/${household.id}/invites`, {
      headers,
      data: { role: "member" },
    })
  ).json();
  const other = await browser.newContext();
  try {
    const joined = await other.request.post(
      `/api/v1/invites/${invite.code}/join`,
      {
        data: {
          username: "film-other-member",
          password: "enhancements-test-passphrase",
          display_name: "合成第二成员",
        },
      },
    );
    expect(joined.ok()).toBeTruthy();
    const otherPage = await other.newPage();
    await otherPage.goto("/albums/on_this_day");
    const otherPlayer = otherPage.getByRole("region", {
      name: "往年今日回忆影片",
    });
    await expect(otherPlayer.locator(".album-film-image")).toBeVisible();
    const otherFilm = await (
      await other.request.get("/api/v1/albums/on_this_day/playback")
    ).json();
    expect(otherFilm.items.map((photo: { id: string }) => photo.id)).toEqual([
      shared,
    ]);
    await page.request.patch(`/api/v1/photos/${shared}`, {
      headers,
      data: { visibility: "private" },
    });
    await expect(otherPlayer.locator(".album-film-image")).toHaveCount(0, {
      timeout: 10000,
    });
    expect(
      (await other.request.get(`/api/v1/photos/${shared}/media/960`)).status(),
    ).toBe(404);
  } finally {
    await other.close();
  }
  expect(errors).toEqual([]);
});

test("推荐偏好可恢复，最近月份入口与相似图空态可用", async ({ page }) => {
  await register(page, "preference-enhancements");
  await page.goto("/preferences");
  await page.getByLabel("开始日期").fill("2024-01-01");
  await page.getByLabel("结束日期").fill("2024-01-31");
  await page.getByLabel("出现方式").selectOption("never");
  await page.getByRole("button", { name: "保存推荐偏好" }).click();
  await expect(page.getByText("推荐偏好已保存", { exact: true })).toBeVisible();
  await expect(
    page.getByText("2024-01-01 — 2024-01-31", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "恢复推荐" }).click();
  await expect(page.getByText("目前没有特别的推荐限制。")).toBeVisible();
  await page.goto("/albums/recent");
  await expect(
    page.getByRole("region", { name: "最近的日子回忆影片" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "对应照片", exact: true }),
  ).toBeVisible();
  await page.goto("/duplicates");
  await expect(
    page.getByRole("heading", { name: "没有待整理的相似照片" }),
  ).toBeVisible();
});

test("组合搜索保存动态相册、人工排除、新照片补充与异步故事", async ({
  page,
}) => {
  const { headers } = await register(page, "collection-browser-regression");
  const ids: string[] = [];
  async function add(index: number, caption: string) {
    const file = await readFile(
      path.resolve(`../../runtime/e2e-samples/synthetic-000${index}.jpg`),
    );
    const uploaded = await page.request.post("/api/v1/photos/upload", {
      headers,
      multipart: {
        shared: "true",
        file: { name: "synthetic.jpg", mimeType: "image/jpeg", buffer: file },
      },
    });
    expect(uploaded.ok()).toBeTruthy();
    const id = (await uploaded.json()).photo_id;
    const changed = await page.request.patch(`/api/v1/photos/${id}`, {
      headers,
      data: {
        title: `合成画面 ${index}`,
        caption_user: caption,
        taken_at: `2024-06-0${index}T03:00:00+00:00`,
      },
    });
    expect(changed.ok()).toBeTruthy();
    return id;
  }
  ids.push(await add(1, "海边合照"));
  ids.push(await add(2, "海边合照"));
  await add(3, "室内聚会");
  await page.goto("/search");
  await page.getByLabel("搜索家庭记忆").fill("2024年夏天海边合照");
  await page.getByRole("button", { name: "寻找记忆", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "找到 2 个候选瞬间" }),
  ).toBeVisible();
  await page.getByLabel("相册名称").fill("合成夏日海边");
  const creation = page.waitForResponse(
    (r) => r.url().endsWith("/collections") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "保存规则，自动补充" }).click();
  const created = await (await creation).json();
  await expect(page).toHaveURL(new RegExp(`/collections/${created.id}$`));
  await expect(
    page.getByRole("heading", { name: "相册全部照片 · 2 张" }),
  ).toBeVisible();
  await expect(page.locator(".album-film-image")).toBeVisible();
  await page.getByRole("checkbox", { name: "合成画面 1", exact: true }).check();
  await page.getByRole("button", { name: "从相册移除所选" }).click();
  await expect(
    page.getByRole("heading", { name: "相册全部照片 · 1 张" }),
  ).toBeVisible();
  ids.push(await add(4, "海边合照"));
  await expect(
    page.getByRole("heading", { name: "相册全部照片 · 2 张" }),
  ).toBeVisible({ timeout: 10000 });
  const current = await (
    await page.request.get(`/api/v1/collections/${created.id}`)
  ).json();
  expect(current.items.map((p: { id: string }) => p.id).sort()).toEqual(
    [ids[1], ids[2]].sort(),
  );
  const composition = await page.request.post("/api/v1/stories/compose", {
    headers,
    data: {
      photo_ids: current.items.map((p: { id: string }) => p.id),
      length: "short",
      title: "合成夏日故事",
    },
  });
  expect(composition.ok()).toBeTruthy();
  const story = (await composition.json()).story;
  expect(
    (await page.request.post("/api/v1/jobs/resume", { headers })).ok(),
  ).toBeTruthy();
  await page.goto(`/stories/${story.id}/edit`);
  await expect(page.getByLabel("故事标题", { exact: true })).toHaveValue(
    "合成夏日故事",
    { timeout: 15000 },
  );
  await page.getByLabel("故事标题", { exact: true }).fill("人工保留的夏日故事");
  await page.getByRole("button", { name: "保存故事", exact: true }).click();
  await expect(page.getByText("故事已保存", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("故事标题", { exact: true })).toHaveValue(
    "人工保留的夏日故事",
  );
});
