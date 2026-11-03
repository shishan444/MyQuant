/**
 * 05 进化中心·停止确认对话框（fe-research-execution-domain 3.2，
 * 视觉规格=原型组件规格 07-M1：标题问句+事实三句+取消/确认停止双钮）。
 * 事实三句按进化任务真实语义改写（07 原文为模拟交易语义）——
 * 停止=协作停止（当前代完整落库后退出）、终态不可恢复（区别于暂停）、
 * 数据保留可查。确认钮红=POLARITY.down 实色（07-M1 $polarity-down 同源）。
 */
import { Button, Modal } from "antd";
import { POLARITY } from "@/theme/tokens";

interface StopConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 待停任务 ID（截断呈现） */
  taskId: string | null;
  onConfirm: () => void;
  loading?: boolean;
}

/** 事实三句（点+句形制）——停止语义如实披露，用户在知情下确认 */
const FACTS = [
  "停止即当前代完整落库后任务退出（协作停止）",
  "停止即终态，任务无法恢复（区别于暂停，暂停的任务才可恢复）",
  "任务数据保留，历史记录与产出策略仍可查看",
];

function StopConfirmDialog({
  open,
  onOpenChange,
  taskId,
  onConfirm,
  loading = false,
}: StopConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      title="停止该任务？"
      width={440}
      footer={[
        <Button key="cancel" disabled={loading} onClick={() => onOpenChange(false)}>
          取消
        </Button>,
        <Button
          key="confirm"
          type="primary"
          loading={loading}
          style={{ backgroundColor: POLARITY.down, borderColor: POLARITY.down }}
          onClick={onConfirm}
          data-testid="stop-confirm-button"
        >
          确认停止
        </Button>,
      ]}
    >
      {taskId && (
        <p className="mb-2 font-mono text-xs text-text-secondary">
          {taskId.slice(0, 8)}
        </p>
      )}
      <ul className="flex flex-col gap-1.5" data-testid="stop-confirm-facts">
        {FACTS.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm text-text-secondary">
            <span className="mt-2 inline-block h-[5px] w-[5px] shrink-0 rounded-full bg-text-muted" />
            {f}
          </li>
        ))}
      </ul>
    </Modal>
  );
}

export { StopConfirmDialog };
export type { StopConfirmDialogProps };
