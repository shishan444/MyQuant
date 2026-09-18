import { useEffect } from "react";
import { TrendingUp } from "lucide-react";
import { Button, Form, InputNumber, Modal, Switch } from "antd";

const LEVERAGE_OPTIONS = [1, 2, 3, 5, 10];

const DIRECTION_OPTIONS: { value: string; label: string }[] = [
  { value: "long", label: "仅多" },
  { value: "short", label: "仅空" },
  { value: "mixed", label: "混合" },
];

interface CreateTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  strategyName: string | null;
  symbol: string;
  timeframe: string;
  initialCash: number;
  leverage: number;
  direction: string;
  onConfirm: (params: { initialCash: number; leverage: number; direction: string; confidenceSizingEnabled: boolean }) => void;
}

interface CreateFormValues {
  initialCash: number;
  leverage: number;
  direction: string;
  confidenceSizing: boolean;
}

export function CreateTaskDialog({
  open,
  onOpenChange,
  strategyName,
  symbol,
  timeframe,
  initialCash: defaultCash,
  leverage: defaultLeverage,
  direction: defaultDirection,
  onConfirm,
}: CreateTaskDialogProps) {
  // 表单草稿唯一持有者=AntD Form（ui-state spec）；弹层开启时同步外部默认值
  const [form] = Form.useForm<CreateFormValues>();

  useEffect(() => {
    if (open) {
      form.setFieldsValue({
        initialCash: defaultCash,
        leverage: defaultLeverage,
        direction: defaultDirection,
        confidenceSizing: false,
      });
    }
  }, [open, defaultCash, defaultLeverage, defaultDirection, form]);

  const handleConfirm = async () => {
    const values = await form.validateFields();
    onConfirm({
      initialCash: values.initialCash,
      leverage: values.leverage,
      direction: values.direction,
      confidenceSizingEnabled: values.confidenceSizing,
    });
  };

  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      title={
        <span className="flex items-center gap-2">
          <TrendingUp className="h-5 w-5" style={{ color: "#F59E0B" }} />
          创建模拟交易任务
        </span>
      }
      width={480}
      footer={[
        <Button key="cancel" onClick={() => onOpenChange(false)}>
          取消
        </Button>,
        <Button key="confirm" type="primary" onClick={handleConfirm}>
          创建任务
        </Button>,
      ]}
    >
      <p className="text-sm text-text-secondary mb-4">
        使用该策略启动模拟交易。运行引擎将先处理历史数据，然后切换至实时模式。
      </p>

      <Form form={form} layout="vertical" className="!mb-0">
        <div className="grid grid-cols-2 gap-3 rounded-lg border border-border-default p-3">
          <div>
            <span className="text-xs text-text-muted">策略</span>
            <p className="font-medium text-text-primary">{strategyName || "未命名策略"}</p>
          </div>
          <div>
            <span className="text-xs text-text-muted">交易对</span>
            <p className="font-medium text-text-primary">
              {symbol} / {timeframe}
            </p>
          </div>

          <Form.Item
            label="初始资金"
            name="initialCash"
            className="!mb-2"
            rules={[{ type: "number", min: 100, message: "初始资金须 ≥100" }]}
          >
            <InputNumber min={100} step={10000} className="w-full font-num" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="杠杆倍数" name="leverage" className="!mb-2">
            <div className="flex gap-1" role="radiogroup" aria-label="杠杆倍数">
              {LEVERAGE_OPTIONS.map((opt) => (
                <Form.Item key={opt} name="leverage" noStyle>
                  <LeverageOption option={opt} />
                </Form.Item>
              ))}
            </div>
          </Form.Item>

          <Form.Item label="交易方向" name="direction" className="!mb-0 col-span-2">
            <div className="flex gap-1" role="radiogroup" aria-label="交易方向">
              {DIRECTION_OPTIONS.map((opt) => (
                <Form.Item key={opt.value} name="direction" noStyle>
                  <DirectionOption option={opt} />
                </Form.Item>
              ))}
            </div>
          </Form.Item>

          <div className="col-span-2 flex items-center justify-between">
            <div>
              <span className="text-xs text-text-muted">置信度仓位缩放</span>
              <p className="text-xs text-text-muted/60">根据信号置信度动态调整入场仓位大小</p>
            </div>
            <Form.Item name="confidenceSizing" valuePropName="checked" className="!mb-0" noStyle>
              <Switch aria-label="置信度仓位缩放" />
            </Form.Item>
          </div>
        </div>
      </Form>
    </Modal>
  );
}

/** 杠杆/方向选项以受控小按钮呈现（Form.Item 包裹取值，点击写回表单） */
function OptionButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`px-2 py-1 rounded text-xs font-medium transition-colors border ${
        active
          ? "text-accent-gold border-accent-gold/40"
          : "border-border-default text-text-muted hover:text-text-primary hover:border-text-muted"
      }`}
      style={active ? { background: "rgba(245,158,11,0.2)" } : undefined}
    >
      {label}
    </button>
  );
}

function LeverageOption({ option, value, onChange }: {
  option: number;
  value?: number;
  onChange?: (v: number) => void;
}) {
  return (
    <OptionButton
      label={`${option}x`}
      active={value === option}
      onClick={() => onChange?.(option)}
    />
  );
}

function DirectionOption({ option, value, onChange }: {
  option: { value: string; label: string };
  value?: string;
  onChange?: (v: string) => void;
}) {
  return (
    <OptionButton
      label={option.label}
      active={value === option.value}
      onClick={() => onChange?.(option.value)}
    />
  );
}
