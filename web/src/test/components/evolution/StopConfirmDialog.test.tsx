/**
 * StopConfirmDialog 组件测（t3r4 G1a——spec R3「停止确认流」三要件锚）。
 * 覆盖：open 态标题问句+事实三句全量+taskId 截断呈现/取消→onOpenChange
 * (false)/确认→onConfirm/loading 态确认钮 loading+取消 disabled。
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StopConfirmDialog } from "@/components/evolution/StopConfirmDialog";

const FACTS = [
  "停止即当前代完整落库后任务退出（协作停止）",
  "停止即终态，任务无法恢复（区别于暂停，暂停的任务才可恢复）",
  "任务数据保留，历史记录与产出策略仍可查看",
];

function setup(over: { open?: boolean; loading?: boolean; taskId?: string | null } = {}) {
  const onOpenChange = vi.fn();
  const onConfirm = vi.fn();
  const utils = render(
    <StopConfirmDialog
      open={over.open ?? true}
      onOpenChange={onOpenChange}
      taskId={over.taskId ?? "dfd85965-781b-4919-b5eb-e5e984446467"}
      onConfirm={onConfirm}
      loading={over.loading ?? false}
    />,
  );
  return { onOpenChange, onConfirm, ...utils };
}

describe("StopConfirmDialog（07-M1 规格三要件）", () => {
  it("open 态：标题问句+事实三句全量+taskId 截断 8 位", async () => {
    setup();
    expect(await screen.findByText("停止该任务？")).toBeInTheDocument();
    const facts = screen.getByTestId("stop-confirm-facts");
    for (const f of FACTS) {
      expect(facts).toHaveTextContent(f);
    }
    expect(screen.getByText("dfd85965")).toBeInTheDocument();
    expect(screen.queryByText(/781b/)).not.toBeInTheDocument(); // 截断呈现非全 ID
  });

  it("取消→onOpenChange(false)；确认停止→onConfirm", async () => {
    const { onOpenChange, onConfirm } = setup();
    fireEvent.click(screen.getByRole("button", { name: /取 消/ }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("stop-confirm-button"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onOpenChange).not.toHaveBeenCalledWith(true);
  });

  it("loading 态：确认钮 loading+取消 disabled", async () => {
    setup({ loading: true });
    const confirmBtn = screen.getByTestId("stop-confirm-button");
    expect(confirmBtn.className).toContain("ant-btn-loading");
    expect(screen.getByRole("button", { name: /取 消/ })).toBeDisabled();
  });
});
