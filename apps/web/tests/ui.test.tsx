import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect } from "vitest";
import { AuthContext, Empty, Notices } from "../src/components/ui";
import { UploadQueue, Upload } from "../src/pages/Library";
import { Settings } from "../src/pages/Admin";
import { StoryPlayer } from "../src/pages/Display";
import { captureLabel, type Auth, type Story } from "../src/api/client";
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  apiAll: vi.fn().mockResolvedValue([]),
  api: vi.fn().mockResolvedValue({
    items: [],
    household: { name: "测试家", timezone: "Asia/Shanghai" },
    settings: { faces_enabled: false, speech_enabled: false },
  }),
}));
function wrap(element: React.ReactNode, role: Auth["role"] = "owner") {
  const value = {
    role,
    user: { id: "user", display_name: "测试家人" },
    csrf: "test",
    household: {
      id: "family",
      name: "测试家",
      timezone: "Asia/Shanghai",
      settings_json: { autoplay: false, speech_enabled: false },
    },
  } as Auth;
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <AuthContext.Provider value={value}>
          <Notices>{element}</Notices>
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
describe("真实交互状态", () => {
  it("空相册提供上传恢复入口", () => {
    wrap(<Empty />);
    expect(
      screen.getByRole("link", { name: /上传第一张照片/ }),
    ).toHaveAttribute("href", "/upload");
  });
  it("显示上传成功、失败和重试", () => {
    const retry = vi.fn();
    wrap(
      <UploadQueue
        items={[
          {
            file: new File(["a"], "a.jpg"),
            shared: true,
            progress: 100,
            state: "success",
          },
          {
            file: new File(["b"], "b.jpg"),
            shared: false,
            progress: 20,
            state: "failed",
            error: "网络断开",
          },
        ]}
        onRetry={retry}
      />,
    );
    expect(screen.getByText(/上传成功/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledWith(1);
    expect(screen.getByText(/网络断开/)).toBeVisible();
  });
  it("viewer 没有上传表单", () => {
    wrap(<Upload />, "viewer");
    expect(screen.getByText("当前账号为只读成员")).toBeVisible();
    expect(screen.queryByText("选择照片")).not.toBeInTheDocument();
  });
  it("共享默认未勾选", () => {
    wrap(<Upload />);
    expect(screen.getByLabelText("加入家庭共享相册")).not.toBeChecked();
    fireEvent.click(screen.getByLabelText("加入家庭共享相册"));
    expect(screen.getByLabelText("加入家庭共享相册")).toBeChecked();
  });
  it("viewer 无管理员设置按钮", async () => {
    wrap(<Settings />, "viewer");
    expect(
      await screen.findByText("此页面的修改操作需要家庭管理员权限。"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /保存隐私/ }),
    ).not.toBeInTheDocument();
  });
  it("故事支持暂停、前后页与退出", () => {
    const close = vi.fn();
    const story = {
      id: "story",
      title: "家庭故事",
      theme: "warm",
      background: "#222222",
      ending: "结束",
      slides: [
        {
          photo_id: "p1",
          caption: "第一页",
          duration_s: 3,
          transition: "fade",
        },
        { photo_id: "p2", caption: "第二页", duration_s: 3, transition: "cut" },
      ],
    } as Story;
    wrap(<StoryPlayer story={story} onClose={close} />);
    fireEvent.click(screen.getByRole("button", { name: "暂停故事" }));
    expect(screen.getByRole("button", { name: "播放故事" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "下一页故事" }));
    expect(screen.getByText("第二页")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "上一页故事" }));
    expect(screen.getByText("第一页")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "退出故事" }));
    expect(close).toHaveBeenCalled();
  });
  it("未知拍摄时间不使用上传日期伪装", () => {
    expect(
      captureLabel({ taken_at: null, capture_time_source: "upload_uncertain" }),
    ).toBe("拍摄时间未知");
  });
  it("显示家庭时区中的日期", () => {
    expect(
      captureLabel({
        taken_at: "2025-01-01T18:00:00+00:00",
        capture_time_source: "user",
        household_timezone: "Asia/Shanghai",
      }),
    ).toContain("2025/1/2");
  });
});
