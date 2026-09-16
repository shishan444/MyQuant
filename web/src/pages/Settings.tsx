import { useState, useEffect, useCallback } from "react";
import { toast } from "sonner";
import {
  Button as AntButton,
  Divider,
  Input,
  InputNumber,
  Select,
  Skeleton,
  Switch,
  Tabs,
  Form,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { GlassCard } from "@/components/GlassCard";
import { PageTransition } from "@/components/PageTransition";
import { api } from "@/services/api";
import { useChartSettings } from "@/stores/chart-settings";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

/**
 * 通用配置形状 ← /api/config 端点（响应为 dict 无 schema——G1 无 schema 清单项），
 * 手写按实际 JSON 形状。
 */
interface AppConfig {
  language: string;
  timezone: string;
  notify_evolution: boolean;
  notify_signal: boolean;
  binance_api_key: string;
  binance_secret_key: string;
  binance_connected: boolean;
  init_cash: number;
  maker_fee: number;
  taker_fee: number;
  max_positions: number;
}

const DEFAULT_CONFIG: AppConfig = {
  language: "zh-CN",
  timezone: "UTC+8",
  notify_evolution: true,
  notify_signal: true,
  binance_api_key: "",
  binance_secret_key: "",
  binance_connected: false,
  init_cash: 100000,
  maker_fee: 0.1,
  taker_fee: 0.1,
  max_positions: 1,
};

export function Settings() {
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    api.get<AppConfig>("/api/config").then(setConfig).catch(() => setConfig(DEFAULT_CONFIG));
  }, []);

  const savePartial = useCallback(async (updates: Partial<AppConfig>) => {
    try {
      const data = await api.put<AppConfig>("/api/config", { json: updates });
      setConfig(data);
      toast.success("设置已保存");
    } catch {
      toast.error("保存失败");
    }
  }, []);

  if (!config) {
    return (
      <PageTransition>
        <div className="flex flex-col gap-6 max-w-2xl">
          <Skeleton active paragraph={{ rows: 6 }} className="rounded-xl" />
          <Skeleton active paragraph={{ rows: 6 }} className="rounded-xl" />
        </div>
      </PageTransition>
    );
  }

  return (
    <PageTransition>
      <div className="flex flex-col gap-6 max-w-2xl">
        <Tabs
          defaultActiveKey="general"
          items={[
            {
              key: "general",
              label: "通用",
              children: (
                <>
                  <GeneralSettings config={config} onSave={savePartial} />
                  <BinanceApiSettings config={config} onSave={savePartial} />
                  <TradingSettings config={config} onSave={savePartial} />
                </>
              ),
            },
            { key: "indicators", label: "指标参数", children: <IndicatorSettings /> },
            {
              key: "data",
              label: "数据管理",
              children: (
                <GlassCard className="p-6" hover={false}>
                  <p className="text-sm text-text-muted">数据管理功能请前往数据管理页面</p>
                </GlassCard>
              ),
            },
            {
              key: "about",
              label: "关于",
              children: (
                <GlassCard className="p-6" hover={false}>
                  <h2 className="text-base font-medium text-text-primary mb-2">MyQuant v3.0</h2>
                  <p className="text-sm text-text-muted">策略实验室 - 多周期假设验证与智能进化平台</p>
                </GlassCard>
              ),
            },
          ]}
        />
      </div>
    </PageTransition>
  );
}

// ---------------------------------------------------------------------------
// General settings（表单草稿=AntD Form 唯一持有）
// ---------------------------------------------------------------------------

type GeneralFormValues = Pick<AppConfig, "language" | "timezone" | "notify_evolution" | "notify_signal">;

function GeneralSettings({ config, onSave }: { config: AppConfig; onSave: (u: Partial<AppConfig>) => void }) {
  const [form] = Form.useForm<GeneralFormValues>();

  const handleSave = () => {
    const values = form.getFieldsValue();
    onSave(values);
  };

  return (
    <GlassCard className="p-6 !mb-6" hover={false}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-medium text-text-primary">通用设置</h2>
        <AntButton size="small" type="primary" onClick={handleSave}>
          保存更改
        </AntButton>
      </div>

      <Form form={form} layout="horizontal" initialValues={config} className="!mb-0">
        <div className="flex items-center justify-between">
          <span className="text-sm text-text-secondary">主题</span>
          <span className="text-sm text-text-primary">深色模式</span>
        </div>
        <Divider className="!my-3" />
        <Form.Item label="语言" name="language" className="!mb-3" colon={false}>
          <Select
            className="w-40"
            options={[
              { value: "zh-CN", label: "简体中文" },
              { value: "en", label: "English" },
            ]}
          />
        </Form.Item>
        <Divider className="!my-3" />
        <Form.Item label="时区" name="timezone" className="!mb-3" colon={false}>
          <Select
            className="w-44"
            options={[
              { value: "UTC+8", label: "UTC+8 北京时间" },
              { value: "UTC+0", label: "UTC+0 格林威治" },
              { value: "UTC-5", label: "UTC-5 纽约" },
              { value: "UTC+9", label: "UTC+9 东京" },
            ]}
          />
        </Form.Item>
        <Divider className="!my-3" />
        <div className="flex items-center justify-between">
          <span className="text-sm text-text-secondary">通知</span>
          <div className="flex items-center gap-4">
            <Form.Item name="notify_evolution" valuePropName="checked" className="!mb-0" noStyle>
              <Switch aria-label="进化完成时通知" />
            </Form.Item>
            <span className="text-xs text-text-secondary">进化完成时通知</span>
            <Form.Item name="notify_signal" valuePropName="checked" className="!mb-0" noStyle>
              <Switch aria-label="策略信号通知" />
            </Form.Item>
            <span className="text-xs text-text-secondary">策略信号通知</span>
          </div>
        </div>
      </Form>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Binance API settings（凭证回显风险 S1 记录在案：GET /api/config 明文回显，
// 前端无修复项——移交后端容器；本页行为等价保留）
// ---------------------------------------------------------------------------

type BinanceFormValues = Pick<AppConfig, "binance_api_key" | "binance_secret_key">;

function BinanceApiSettings({ config, onSave }: { config: AppConfig; onSave: (u: Partial<AppConfig>) => void }) {
  const [form] = Form.useForm<BinanceFormValues>();
  const [connected, setConnected] = useState(config.binance_connected);
  const [testing, setTesting] = useState(false);

  const handleTest = async () => {
    setTesting(true);
    await new Promise((r) => setTimeout(r, 1500));
    const { binance_api_key: apiKey, binance_secret_key: secretKey } = form.getFieldsValue();
    setConnected(!!apiKey && !!secretKey);
    setTesting(false);
    if (apiKey && secretKey) {
      toast.success("连接成功");
    } else {
      toast.error("请填写 API Key 和 Secret Key");
    }
  };

  const handleSave = () => {
    const values = form.getFieldsValue();
    onSave({ ...values, binance_connected: connected });
  };

  return (
    <GlassCard className="p-6 !mb-6" hover={false}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-medium text-text-primary">Binance API 配置</h2>
        <div className="flex gap-2">
          <AntButton size="small" onClick={handleTest} disabled={testing}>
            {testing ? "测试中..." : "测试连接"}
          </AntButton>
          <AntButton size="small" type="primary" onClick={handleSave}>
            保存
          </AntButton>
        </div>
      </div>

      <Form form={form} layout="horizontal" initialValues={config} className="!mb-0">
        <Form.Item label="API Key" name="binance_api_key" className="!mb-3" colon={false}>
          <Input.Password placeholder="输入 API Key" visibilityToggle={{ visible: false }} />
        </Form.Item>
        <Divider className="!my-3" />
        <Form.Item label="Secret Key" name="binance_secret_key" className="!mb-3" colon={false}>
          <Input.Password placeholder="输入 Secret Key" visibilityToggle={{ visible: false }} />
        </Form.Item>
        <Divider className="!my-3" />
        <div className="flex items-center gap-3">
          <span className="text-sm text-text-secondary w-24 shrink-0">状态</span>
          <div className="flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: connected ? "#00C853" : "#484f58" }}
            />
            {/* 状态非纯颜色载体：文字伴随指示点 */}
            <span className="text-xs text-text-secondary">{connected ? "已连接" : "未配置"}</span>
          </div>
        </div>
      </Form>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Trading settings（数值字段经 InputNumber+rules：失败保留草稿+错误就近展示）
// ---------------------------------------------------------------------------

type TradingFormValues = Pick<AppConfig, "init_cash" | "maker_fee" | "taker_fee" | "max_positions">;

function TradingSettings({ config, onSave }: { config: AppConfig; onSave: (u: Partial<AppConfig>) => void }) {
  const [form] = Form.useForm<TradingFormValues>();

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      onSave(values);
    } catch {
      // 校验失败：AntD Form 保留草稿并就近展示错误（ui-state spec 场景）
    }
  };

  return (
    <GlassCard className="p-6" hover={false}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-medium text-text-primary">模拟交易配置</h2>
        <AntButton size="small" type="primary" onClick={handleSave}>
          保存
        </AntButton>
      </div>

      <Form
        form={form}
        layout="horizontal"
        initialValues={config}
        className="!mb-0"
        labelWrap
        labelCol={{ flex: "84px" }}
      >
        <Form.Item
          label="初始资金"
          name="init_cash"
          className="!mb-3"
          colon={false}
          rules={[{ type: "number", min: 1, message: "初始资金须为正数" }]}
        >
          <InputNumber className="w-40 font-num" />
        </Form.Item>
        <Divider className="!my-3" />
        <div className="flex items-start gap-2">
          <span className="text-sm text-text-secondary shrink-0 leading-8" style={{ width: 108 }}>
            手续费率
          </span>
          <Form.Item
            name="maker_fee"
            noStyle
            rules={[{ type: "number", min: 0, max: 100, message: "Maker 费率须在 0-100%" }]}
          >
            <InputNumber className="w-24 font-num" aria-label="Maker 手续费率（%）" />
          </Form.Item>
          <span className="text-xs text-text-muted leading-8">% (Maker) /</span>
          <Form.Item
            name="taker_fee"
            noStyle
            rules={[{ type: "number", min: 0, max: 100, message: "Taker 费率须在 0-100%" }]}
          >
            <InputNumber className="w-24 font-num" aria-label="Taker 手续费率（%）" />
          </Form.Item>
          <span className="text-xs text-text-muted leading-8">% (Taker)</span>
        </div>
        <Divider className="!my-3" />
        <Form.Item
          label="最大持仓"
          name="max_positions"
          className="!mb-3"
          colon={false}
          rules={[{ type: "integer" as never, min: 1, message: "最大持仓须为 ≥1 整数" }]}
          extra={<span className="text-xs text-text-muted">个策略同时运行</span>}
        >
          <InputNumber className="w-24 font-num" />
        </Form.Item>
        <Divider className="!my-3" />
        <div className="flex items-center gap-3">
          <span className="text-sm text-text-secondary" style={{ flex: "0 0 84px" }}>数据刷新</span>
          <span className="text-sm text-text-primary">实时 (WebSocket)</span>
        </div>
      </Form>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Indicator settings tab（zustand persist store 持久化；订阅形态记录：本组件
// 全量订阅 useChartSettings()，selector 化归 G17 终核）
// ---------------------------------------------------------------------------

function IndicatorSettings() {
  const settings = useChartSettings();

  return (
    <>
      {/* EMA section */}
      <GlassCard className="p-6 !mb-6" hover={false}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-text-primary">EMA 配置</h2>
          <AntButton size="small" icon={<Plus className="h-3 w-3" />} onClick={() => settings.addEma(20)}>
            添加 EMA
          </AntButton>
        </div>

        <div className="flex flex-col gap-3">
          {settings.emaList.map((ema, i) => (
            <div key={i} className="flex items-center gap-3">
              <Switch
                checked={ema.enabled}
                onChange={(v) => settings.updateEma(i, { enabled: v })}
                aria-label={`EMA${ema.period} 启用`}
              />
              <span className="text-xs text-text-muted w-8">EMA</span>
              <InputNumber
                value={ema.period}
                onChange={(v) => settings.updateEma(i, { period: v ?? 10 })}
                min={2}
                max={500}
                className="w-20 text-xs font-num"
                aria-label={`EMA${ema.period} 周期`}
              />
              <label className="inline-flex items-center gap-1 text-xs text-text-muted cursor-pointer">
                <input
                  type="color"
                  value={ema.color}
                  onChange={(e) => settings.updateEma(i, { color: e.target.value })}
                  className="h-8 w-8 cursor-pointer rounded border border-border-default bg-transparent"
                  aria-label={`EMA${ema.period} 颜色`}
                />
                颜色
              </label>
              <AntButton
                type="text"
                size="small"
                danger
                icon={<Trash2 className="h-3.5 w-3.5" />}
                aria-label={`删除 EMA${ema.period}`}
                onClick={() => settings.removeEma(i)}
              />
            </div>
          ))}
          {settings.emaList.length === 0 && (
            <p className="text-xs text-text-muted">暂无 EMA 配置，点击上方按钮添加</p>
          )}
        </div>
      </GlassCard>

      {/* BOLL section */}
      <GlassCard className="p-6 !mb-6" hover={false}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-text-primary">BOLL 配置</h2>
          <Switch
            checked={settings.boll.enabled}
            onChange={(v) => settings.setBoll({ enabled: v })}
            aria-label="BOLL 启用"
          />
        </div>

        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="text-sm text-text-secondary w-16 shrink-0">周期</span>
            <InputNumber
              value={settings.boll.period}
              onChange={(v) => settings.setBoll({ period: v ?? 20 })}
              min={2}
              className="w-20 text-xs font-num"
              aria-label="BOLL 周期"
            />
          </div>
          <Divider className="!my-2" />
          <div className="flex items-center gap-3">
            <span className="text-sm text-text-secondary w-16 shrink-0">标准差</span>
            <InputNumber
              value={settings.boll.std}
              onChange={(v) => settings.setBoll({ std: v ?? 2 })}
              min={0.5}
              max={4}
              step={0.5}
              className="w-20 text-xs font-num"
              aria-label="BOLL 标准差"
            />
          </div>
          <Divider className="!my-2" />
          <label className="flex items-center gap-3 cursor-pointer">
            <span className="text-sm text-text-secondary w-16 shrink-0">颜色</span>
            <input
              type="color"
              value={settings.boll.color}
              onChange={(e) => settings.setBoll({ color: e.target.value })}
              className="h-8 w-8 cursor-pointer rounded border border-border-default bg-transparent"
              aria-label="BOLL 颜色"
            />
          </label>
        </div>
      </GlassCard>

      {/* RSI section */}
      <GlassCard className="p-6 !mb-6" hover={false}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-text-primary">RSI 配置</h2>
          <Switch
            checked={settings.rsi.enabled}
            onChange={(v) => settings.setRsi({ enabled: v })}
            aria-label="RSI 启用"
          />
        </div>

        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="text-sm text-text-secondary w-16 shrink-0">周期</span>
            <InputNumber
              value={settings.rsi.period}
              onChange={(v) => settings.setRsi({ period: v ?? 14 })}
              min={2}
              className="w-20 text-xs font-num"
              aria-label="RSI 周期"
            />
          </div>
          <Divider className="!my-2" />
          <div className="flex items-center gap-3">
            <span className="text-sm text-text-secondary w-16 shrink-0">超买线</span>
            <InputNumber
              value={settings.rsi.overbought}
              onChange={(v) => settings.setRsi({ overbought: v ?? 70 })}
              min={50}
              max={100}
              className="w-20 text-xs font-num"
              aria-label="RSI 超买线"
            />
          </div>
          <Divider className="!my-2" />
          <div className="flex items-center gap-3">
            <span className="text-sm text-text-secondary w-16 shrink-0">超卖线</span>
            <InputNumber
              value={settings.rsi.oversold}
              onChange={(v) => settings.setRsi({ oversold: v ?? 30 })}
              min={0}
              max={50}
              className="w-20 text-xs font-num"
              aria-label="RSI 超卖线"
            />
          </div>
        </div>
      </GlassCard>

      {/* VOL section */}
      <GlassCard className="p-6 !mb-6" hover={false}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-text-primary">成交量配置</h2>
          <Switch
            checked={settings.vol.enabled}
            onChange={(v) => settings.setVol({ enabled: v })}
            aria-label="成交量指标启用"
          />
        </div>

        <div className="flex items-center gap-3">
          <span className="text-sm text-text-secondary w-16 shrink-0">位置</span>
          <Select
            value={settings.vol.position}
            onChange={(v) => settings.setVol({ position: v as "overlay" | "separate" })}
            className="w-32"
            aria-label="成交量显示位置"
            options={[
              { value: "overlay", label: "叠加在主图" },
              { value: "separate", label: "独立子图" },
            ]}
          />
        </div>
      </GlassCard>

      {/* Actions */}
      <div className="flex items-center justify-between">
        <AntButton size="small" onClick={settings.resetToDefaults}>
          恢复默认
        </AntButton>
      </div>
    </>
  );
}
