import { Button, Modal } from "antd";
import type { ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "default" | "destructive";
  onConfirm: () => void;
  loading?: boolean;
  /** 可选自定义内容（如表单），渲染在描述与操作区之间。 */
  children?: ReactNode;
}

function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "确认",
  cancelLabel = "取消",
  variant = "default",
  onConfirm,
  loading = false,
  children,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      title={title}
      width={440}
      footer={[
        <Button key="cancel" disabled={loading} onClick={() => onOpenChange(false)}>
          {cancelLabel}
        </Button>,
        <Button
          key="confirm"
          type="primary"
          danger={variant === "destructive"}
          onClick={onConfirm}
          loading={loading}
        >
          {confirmLabel}
        </Button>,
      ]}
    >
      {description != null && (
        <p className="text-sm text-text-secondary mb-2">{description}</p>
      )}
      {children}
    </Modal>
  );
}

export { ConfirmDialog };
export type { ConfirmDialogProps };
