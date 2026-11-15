import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AlbumPlayer } from "../src/components/AlbumPlayer";
import { api } from "../src/api/client";

vi.mock("../src/api/client", async (original) => ({
  ...(await original<typeof import("../src/api/client")>()),
  api: vi.fn(),
}));
const feed = {
  items: ["一", "二"].map((label, i) => ({
    id: String(i),
    title: "回忆" + label,
    media_url: `/image-${i}`,
    thumbnail_url: `/thumb-${i}`,
    taken_at: null,
    visibility: "private",
  })),
  total: 2,
  source_total: 81,
  interval_seconds: 3,
};
beforeEach(() => {
  vi.mocked(api).mockReset();
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
});
afterEach(() => vi.useRealTimers());
function show() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AlbumPlayer title="成长" endpoint="/albums/growth/playback" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}
it("影片自动换片，照片总数独立于精选数量", async () => {
  vi.useFakeTimers();
  vi.mocked(api).mockResolvedValue(feed as never);
  show();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50);
  });
  expect(screen.getByAltText("回忆一")).toBeVisible();
  fireEvent.load(screen.getByAltText("回忆一"));
  expect(screen.getByText(/81 张照片中精选 2 张/)).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  expect(screen.getByAltText("回忆二")).toBeVisible();
});
it("后台刷新保持暂停，重新授权失败立即清空缓存图片", async () => {
  vi.mocked(api).mockResolvedValue(feed as never);
  const client = show();
  await screen.findByAltText("回忆一");
  fireEvent.click(screen.getByRole("button", { name: "暂停相册影片" }));
  await act(async () => {
    await client
      .invalidateQueries({ queryKey: ["album-film"] })
      .catch(() => {});
  });
  expect(screen.getByRole("button", { name: "播放相册影片" })).toBeVisible();
  vi.mocked(api).mockRejectedValue(new Error("权限已变化"));
  await act(async () => {
    await client
      .invalidateQueries({ queryKey: ["album-film"] })
      .catch(() => {});
  });
  await waitFor(() =>
    expect(screen.queryByRole("img")).not.toBeInTheDocument(),
  );
  expect(screen.getByText(/无法验证照片权限，已停止播放/)).toBeVisible();
});

it("标签隐藏后清空媒体，返回时等待新授权结果并保持暂停", async () => {
  vi.mocked(api).mockResolvedValue(feed as never);
  show();
  await screen.findByAltText("回忆一");
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: true,
  });
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await act(async () => {
    finish({ ...feed, items: [feed.items[1]] });
  });
  expect(await screen.findByAltText("回忆二")).toBeVisible();
  expect(screen.getByRole("button", { name: "播放相册影片" })).toBeVisible();
});
