import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { api, apiAll, type Auth } from "../src/api/client";
import { AuthContext, Notices } from "../src/components/ui";
import { DiscoverSearch, PhotoTextEditor } from "../src/pages/Discover";

vi.mock("../src/api/client", async (original) => ({
  ...(await original<typeof import("../src/api/client")>()),
  api: vi.fn(),
  apiAll: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(apiAll).mockReset();
});
function show(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthContext.Provider
          value={
            {
              role: "owner",
              user: { id: "u" },
              household: {
                id: "h",
                name: "测试家",
                timezone: "Asia/Shanghai",
                settings_json: { speech_enabled: false },
              },
            } as Auth
          }
        >
          <Notices>{node}</Notices>
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}
const result = {
  items: [
    {
      id: "p",
      title: "海边",
      taken_at: "2025-07-01T00:00:00Z",
      thumbnail_url: "/p.jpg",
      media_url: "/p.jpg",
      visibility: "family",
      analysis: null,
    },
  ],
  total: 1,
  page: 1,
  page_size: 40,
  query_id: null,
  semantic_state: "disabled",
  unknown_conditions: [],
  parsed_conditions: {
    start: null,
    end: null,
    terms: ["海边"],
    exclude_terms: [],
    people: [],
    people_mode: "all",
  },
};

it("组合查询明确发送多人交集与排除内容", async () => {
  vi.mocked(apiAll).mockResolvedValue([
    { id: "a", name: "小明", manually_confirmed: true },
    { id: "b", name: "妈妈", manually_confirmed: true },
  ] as never);
  vi.mocked(api).mockResolvedValue(result as never);
  show(<DiscoverSearch />);
  await screen.findByRole("option", { name: "小明" });
  await userEvent.selectOptions(screen.getByLabelText("人物条件"), ["a", "b"]);
  fireEvent.change(screen.getByLabelText("搜索家庭记忆"), {
    target: { value: "去年夏天海边" },
  });
  fireEvent.change(screen.getByLabelText("排除内容"), {
    target: { value: "截图，票据" },
  });
  fireEvent.click(screen.getByRole("button", { name: "寻找记忆" }));
  await screen.findByRole("heading", { name: "找到 1 个候选瞬间" });
  expect(api).toHaveBeenCalledWith(
    "/search",
    "POST",
    expect.objectContaining({
      person_ids: ["a", "b"],
      people_mode: "all",
      exclude_terms: ["截图", "票据"],
    }),
  );
});

it("语义任务等待时禁止保存不完整静态集合", async () => {
  vi.mocked(apiAll).mockResolvedValue([]);
  let count = 0;
  vi.mocked(api).mockImplementation(async () => {
    if (count++ === 0)
      return { ...result, query_id: "q", semantic_state: "queued" } as never;
    return new Promise(() => {});
  });
  show(<DiscoverSearch />);
  fireEvent.click(screen.getByRole("button", { name: "寻找记忆" }));
  expect(
    await screen.findByRole("button", { name: "保存这批照片" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存这批照片" }));
  expect(
    vi.mocked(api).mock.calls.every((call) => call[0] !== "/collections"),
  ).toBe(true);
});

it("文字区域可高亮，后台刷新保留未保存校对", async () => {
  const original = {
    text: {
      text: "北京站",
      corrected_text: null,
      confirmed: false,
      regions_json: [{ text: "北京站", bbox: [0.1, 0.2, 0.5, 0.1] }],
      model_version: "1",
    },
    job: null,
  };
  vi.mocked(api).mockImplementation(
    async (path) =>
      (path.endsWith("/text")
        ? original
        : { id: "p", media_url: "/p.jpg" }) as never,
  );
  const client = show(<PhotoTextEditor photoId="p" />);
  const input = await screen.findByRole("textbox", { name: "图片文字校对" });
  await waitFor(() => expect(input).toHaveValue("北京站"));
  fireEvent.change(input, { target: { value: "上海站，人工正在修改" } });
  act(() =>
    client.setQueryData(["photo-text", "p"], {
      ...original,
      text: { ...original.text, text: "北京南站" },
    }),
  );
  expect(input).toHaveValue("上海站，人工正在修改");
  fireEvent.click(screen.getByText("查看识别区域"));
  const region = screen.getByRole("button", { name: "高亮文字区域 1：北京站" });
  expect(region).toHaveStyle({
    left: "10%",
    top: "20%",
    width: "50%",
    height: "10%",
  });
  fireEvent.click(region);
  expect(region).toHaveAttribute("aria-pressed", "true");
});
