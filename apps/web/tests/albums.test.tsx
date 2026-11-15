import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { Home, Photos } from "../src/pages/Library";
import { Album } from "../src/pages/Albums";
import { AuthContext, Notices } from "../src/components/ui";
import { api, type Auth } from "../src/api/client";

vi.mock("../src/api/client", async (original) => ({
  ...(await original<typeof import("../src/api/client")>()),
  api: vi.fn(),
  apiAll: vi.fn().mockResolvedValue([]),
}));

beforeEach(() => {
  vi.mocked(api).mockReset();
});

function show(element: React.ReactNode, path = "/") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <AuthContext.Provider
          value={
            {
              role: "owner",
              user: { id: "u" },
              household: { name: "测试家", timezone: "Asia/Shanghai" },
            } as Auth
          }
        >
          <Notices>
            <Routes>
              <Route path="/" element={element} />
              <Route path="/albums/:kind" element={element} />
            </Routes>
          </Notices>
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("首页三个入口始终指向独立相册，计数不取播放列表", async () => {
  vi.mocked(api).mockImplementation(
    async (path) =>
      (path === "/albums"
        ? {
            items: [
              {
                kind: "on_this_day",
                total: 18,
                mode: "automatic",
                cover: null,
              },
              { kind: "growth", total: 7, mode: "manual", cover: null },
              { kind: "travel", total: 0, mode: "automatic", cover: null },
            ],
          }
        : { items: [] }) as never,
  );
  show(<Home />);
  expect(await screen.findByText("18 张照片 · 已自动整理")).toBeVisible();
  expect(screen.getByRole("link", { name: /往年今日/ })).toHaveAttribute(
    "href",
    "/albums/on_this_day",
  );
  expect(screen.getByRole("link", { name: /慢慢长大/ })).toHaveAttribute(
    "href",
    "/albums/growth",
  );
  expect(screen.getByRole("link", { name: /一起走过/ })).toHaveAttribute(
    "href",
    "/albums/travel",
  );
  expect(
    screen.queryByText("先在家庭人物中指定成长人物"),
  ).not.toBeInTheDocument();
});

it("成长相册无候选时说明自动整理，不要求先配置也不填充照片", async () => {
  vi.mocked(api).mockResolvedValue({
    items: [],
    total: 0,
    mode: "automatic",
    pending_count: 2,
  } as never);
  show(<Album />, "/albums/growth");
  expect(
    await screen.findByText(/历史照片与新照片正在使用本地模型依次分析/),
  ).toBeVisible();
  expect(screen.getByText(/2 张照片仍在后台分析/)).toBeVisible();
  expect(
    screen.getByRole("link", { name: "查看照片与处理状态" }),
  ).toHaveAttribute("href", "/photos");
  expect(document.querySelectorAll(".photo-card")).toHaveLength(0);
});

it("本页多选本人照片，批量共享必须明确确认", async () => {
  vi.mocked(api).mockImplementation(
    async (path) =>
      ({
        items: path.startsWith("/photos?")
          ? [
              {
                id: "a",
                title: "私密一",
                uploader_id: "u",
                visibility: "private",
                taken_at: null,
              },
              {
                id: "b",
                title: "私密二",
                uploader_id: "u",
                visibility: "private",
                taken_at: null,
              },
              {
                id: "c",
                title: "他人共享",
                uploader_id: "another",
                visibility: "family",
                taken_at: null,
              },
            ]
          : [],
        total: 3,
      }) as never,
  );
  const confirm = vi
    .spyOn(window, "confirm")
    .mockReturnValueOnce(false)
    .mockReturnValueOnce(true);
  show(<Photos />);
  fireEvent.click(
    await screen.findByRole("button", { name: "选中本页我的照片" }),
  );
  expect(screen.getByText("已选 2 张")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "加入家庭共享" }));
  expect(vi.mocked(api).mock.calls.some(([p]) => p === "/photos/batch")).toBe(
    false,
  );
  fireEvent.click(screen.getByRole("button", { name: "加入家庭共享" }));
  expect(api).toHaveBeenCalledWith("/photos/batch", "POST", {
    action: "share",
    photo_ids: ["a", "b"],
  });
  confirm.mockRestore();
});

it("已有本人私密回忆时首页提供相册封面，私密回顾链接不进入相框", async () => {
  vi.mocked(api).mockImplementation(
    async (path) =>
      (path === "/albums"
        ? {
            private_photo_count: 1,
            items: [
              {
                kind: "on_this_day",
                title: "往年今日",
                total: 1,
                mode: "automatic",
                cover: {
                  id: "private-photo",
                  visibility: "private",
                  title: "私密旧照",
                  taken_at: "2023-11-15T03:00:00Z",
                  media_url: "/private.jpg",
                  thumbnail_url: "/private-thumb.jpg",
                },
              },
            ],
          }
        : { items: [] }) as never,
  );
  show(<Home />);
  expect(
    await screen.findByRole("link", { name: /回顾自动整理的记忆/ }),
  ).toHaveAttribute("href", "/albums/on_this_day");
  expect(screen.getByRole("heading", { name: "私密旧照" })).toBeVisible();
  expect(screen.queryByText("就是故事的开始。")).not.toBeInTheDocument();
});

it("往年今日按年份展示结果与归类依据", async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        id: "old",
        title: "旧照片",
        thumbnail_url: "/old.jpg",
        taken_at: "2024-11-15T03:00:00Z",
        album_group: "2024",
        album_group_title: "2024 年 · 2年前的今天",
        album_reason: "2年前的今天",
      },
    ],
    total: 1,
    mode: "automatic",
    pending_count: 0,
    match_window_days: 0,
    date_basis: "2026-11-15",
  } as never);
  show(<Album />, "/albums/on_this_day");
  expect(
    await screen.findByRole("heading", { name: /2024 年 · 2年前的今天/ }),
  ).toBeVisible();
  expect(screen.getByRole("link", { name: /旧照片/ })).toHaveAttribute(
    "href",
    "/photos/old",
  );
  expect(screen.getByLabelText("回忆日期")).toHaveValue("2026-11-15");
});
