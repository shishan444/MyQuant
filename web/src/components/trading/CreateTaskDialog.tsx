import { useEffect, useMemo } from "react";
import { TrendingUp } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button, Form, Input, InputNumber, Modal, Select, Switch } from "antd";
import { useStrategies } from "@/hooks/useStrategies";
import type { CreateTradingTaskParams } from "@/services/trading";

interface CreateTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 入口跳转预选策略（Strategies/Verify 页「模拟交易」带入 strategy_id） */
  presetStrategyId?: string | null;
  onConfirm: (params: CreateTradingTaskParams) => void;
}

interface CreateFormValues {
  strategyId: string;
  initialCash?: number;
  leverage?: number;
  symbol?: string;
  fee?: number;
  slippage?: number;
  windowBars?: number;
  unlimitedWindow: boolean;
  stopLossPct?: number;
}

/**
 * 创建纸盘任务（上盘流转形制）：只收策略编号+可选覆写——DNA 由后端从
 * 策略库快照，timeframe/direction 恒从 DNA 抄录（无参数位，传入即 422）。
 * 覆写面：symbol/initial_cash/fee/slippage/leverage/window_bars/stop_loss_pct。
 */
export function CreateTaskDialog({
  open,
  onOpenChange,
  presetStrategyId,
  onConfirm,
}: CreateTaskDialogProps) {
  // 表单草稿唯一持有者=AntD Form（ui-state spec）；弹层开启时同步外部默认值
  const [form] = Form.useForm<CreateFormValues>();

  useEffect(() => {
    if (open) {
      form.setFieldsValue({
        strategyId: presetStrategyId ?? "",
        initialCash: 100_000,
        leverage: undefined,
        symbol: undefined,
        fee: 0.001,
        slippage: 0.0005,
        windowBars: 180,
        unlimitedWindow: false,
        stopLossPct: undefined,
      });
    }
  }, [open, presetStrategyId, form]);

  // 策略编号选择面（远程列表——GET /api/strategies）
  const { data: strategyList, isLoading: strategiesLoading } = useQuery(
    useStrategies({ limit: 200 }),
  );
  const strategyOptions = useMemo(
    () =>
      (strategyList?.items ?? []).map((s) => ({
        value: s.strategy_id,
        label: s.name ? `${s.name}（${s.symbol}/${s.timeframe}）` : `${s.strategy_id}（${s.symbol}/${s.timeframe}）`,
      })),
    [strategyList],
  );

  const handleConfirm = async () => {
    const values = await form.validateFields();
    // 覆写面只带显式填写项（undefined 项不传=后端取缺省/DNA；JSON 序列化自动剔除）
    const params: CreateTradingTaskParams = {
      strategy_id: values.strategyId,
    };
    if (values.initialCash != null) params.initial_cash = values.initialCash;
    if (values.leverage != null) params.leverage = values.leverage;
    if (values.symbol) params.symbol = values.symbol.trim().toUpperCase();
    if (values.fee != null) params.fee = values.fee;
    if (values.slippage != null) params.slippage = values.slippage;
    // 窗口四形态：不限期开关=显式 null；未开且未填=不传（后端缺省 180）
    if (values.unlimitedWindow) params.window_bars = null;
    else if (values.windowBars != null) params.window_bars = values.windowBars;
    if (values.stopLossPct != null) params.stop_loss_pct = values.stopLossPct;
    onConfirm(params);
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
      width={520}
      footer={[
        <Button key="cancel" onClick={() => onOpenChange(false)}>
          取消
        </Button>,
        <Button key="confirm" type="primary" onClick={handleConfirm}>
          创建任务
        </Button>
      ]}
    >
      <p className="text-sm text-text-secondary mb-4">
        选定策略编号后上盘：策略 DNA 由后端从策略库流转快照，交易方向与周期恒取 DNA（不可覆写）。
        受理后进入 pending 排队，超限自动等待。
      </p>

      <Form form={form} layout="vertical" className="!mb-0">
        <Form.Item
          label="策略"
          name="strategyId"
          className="!mb-3"
          rules={[{ required: true, message: "请选择策略" }]}
        >
          <Select
            showSearch
            allowClear
            placeholder="搜索并选择策略"
            options={strategyOptions}
            loading={strategiesLoading}
            optionFilterProp="label"
          />
        </Form.Item>

        <div className="grid grid-cols-2 gap-3 rounded-lg border border-border-default p-3">
          <Form.Item
            label="初始资金"
            name="initialCash"
            className="!mb-2"
            rules={[{ type: "number", min: 100, message: "初始资金须 ≥100" }]}
          >
            <InputNumber min={100} step={10000} className="w-full font-num" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="杠杆倍数（留空取 DNA）" name="leverage" className="!mb-2">
            <InputNumber min={1} max={125} precision={0} placeholder="取 DNA" className="w-full font-num" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="交易对覆写（留空取 DNA）" name="symbol" className="!mb-2">
            <Input placeholder="如 BTCUSDT" allowClear />
          </Form.Item>

          <Form.Item label="止损比例（留空不设）" name="stopLossPct" className="!mb-2">
            <InputNumber min={0.01} max={1} step={0.05} placeholder="如 0.2" className="w-full font-num" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="手续费率" name="fee" className="!mb-2">
            <InputNumber min={0} step={0.0005} className="w-full font-num" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="滑点" name="slippage" className="!mb-2">
            <InputNumber min={0} step={0.0001} className="w-full font-num" style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item label="窗口根数（bar 数）" name="windowBars" className="!mb-0">
            <InputNumber min={1} precision={0} placeholder="180" className="w-full font-num" style={{ width: "100%" }} disabled={form.getFieldValue("unlimitedWindow")} />
          </Form.Item>

          <div className="flex items-center justify-between">
            <div>
              <span className="text-xs text-text-muted">不限期</span>
              <p className="text-xs text-text-muted/60">不自动结题，手动停止</p>
            </div>
            <Form.Item name="unlimitedWindow" valuePropName="checked" className="!mb-0" noStyle>
              <Switch aria-label="不限期" onChange={() => form.validateFields(["windowBars"]).catch(() => {})} />
            </Form.Item>
          </div>
        </div>
      </Form>
    </Modal>
  );
}
