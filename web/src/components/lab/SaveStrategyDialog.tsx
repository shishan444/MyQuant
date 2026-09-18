import { useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, Input } from "antd";

interface SaveStrategyDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (data: { name: string; description: string; tags: string }) => void;
  pair: string;
  timeframe: string;
  matchRate: number;
  totalCount: number;
}

export function SaveStrategyDialog({
  open,
  onClose,
  onSave,
  pair,
  timeframe,
  matchRate,
  totalCount,
}: SaveStrategyDialogProps) {
  const [name, setName] = useState(`${pair} ${timeframe} 验证策略`);
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("lab,mtf");
  const [saving, setSaving] = useState(false);

  if (!open) return null;

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    onSave({ name: name.trim(), description: description.trim(), tags: tags.trim() });
    setSaving(false);
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/40"
        onClick={onClose}
      />
      {/* Dialog */}
      <div
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[400px] -translate-x-1/2 -translate-y-1/2",
          "border border-border-default bg-bg-surface rounded-xl shadow-2xl",
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border-default px-4 py-3">
          <h3 className="text-sm font-semibold text-text-primary">保存为策略</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-text-muted transition-colors hover:bg-white/5 hover:text-text-primary"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 flex flex-col gap-4">
          {/* Auto-filled info */}
          <div className="flex items-center gap-4 text-xs text-text-muted">
            <span>{pair} / {timeframe}</span>
            <span className={matchRate >= 50 ? "text-profit" : "text-loss"}>
              符合率 {matchRate}%
            </span>
            <span>{totalCount} 次触发</span>
          </div>

          {/* Name */}
          <div>
            <label className="mb-1 block text-xs font-medium text-text-muted">策略名称 *</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="!text-xs"
              placeholder="输入策略名称"
              aria-label="策略名称"
            />
          </div>

          {/* Description */}
          <div>
            <label className="mb-1 block text-xs font-medium text-text-muted">描述</label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="!text-xs"
              placeholder="策略描述 (可选)"
              aria-label="策略描述"
            />
          </div>

          {/* Tags */}
          <div>
            <label className="mb-1 block text-xs font-medium text-text-muted">标签</label>
            <Input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="!text-xs"
              placeholder="用逗号分隔"
              aria-label="标签"
            />
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-2">
            <Button size="small" onClick={onClose}>
              取消
            </Button>
            <Button
              type="primary"
              size="small"
              onClick={handleSave}
              disabled={!name.trim() || saving}
            >
              {saving ? "保存中..." : "确认保存"}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
