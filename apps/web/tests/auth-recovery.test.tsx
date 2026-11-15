import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { AuthPage, CreateHousehold } from "../src/pages/Auth";
import { api } from "../src/api/client";

vi.mock("../src/api/client", async (original) => ({
  ...(await original<typeof import("../src/api/client")>()),
  api: vi.fn(),
}));
beforeEach(() => vi.mocked(api).mockReset());

function show(path: string) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/households/new" element={<CreateHousehold />} />
          <Route path="/" element={<div>已进入新家庭</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const account = {
  user: { id: "kept-user", display_name: "原账号" },
  role: "account",
  household: null,
  csrf: "test-csrf",
};

it("无家庭账号登录后进入创建页，不要求重新注册账号", async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/auth/status") return { needs_setup: false } as never;
    if (path === "/auth/login")
      return { csrf: "test-csrf", needs_household: true } as never;
    return account as never;
  });
  show("/login");
  fireEvent.change(screen.getByLabelText("用户名"), {
    target: { value: "kept-user" },
  });
  fireEvent.change(screen.getByLabelText("口令"), {
    target: { value: "test-only-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(
    await screen.findByRole("heading", { name: "为账号创建一个新家庭" }),
  ).toBeVisible();
  expect(screen.getByText(/账号和原口令仍然有效/)).toBeVisible();
});

it("新家庭创建失败显示错误，重试成功后进入家庭", async () => {
  let attempts = 0;
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (path === "/households" && method === "POST") {
      if (++attempts === 1) throw new Error("创建失败，请重试");
      return { csrf: "new-csrf" } as never;
    }
    return account as never;
  });
  show("/households/new");
  const name = await screen.findByLabelText("家庭名称");
  fireEvent.change(name, { target: { value: "新的测试家庭" } });
  fireEvent.click(screen.getByRole("button", { name: "创建新家庭" }));
  expect(await screen.findByText("创建失败，请重试")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "创建新家庭" }));
  expect(await screen.findByText("已进入新家庭")).toBeVisible();
  expect(api).toHaveBeenCalledWith("/households", "POST", {
    name: "新的测试家庭",
  });
});
