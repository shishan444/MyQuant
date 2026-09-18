import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { Play, ChevronDown, Plus, X } from "lucide-react";
import { Badge, Button, Input, InputNumber, Select as AntSelect } from "antd";
import { cn } from "@/lib/utils";
import {
  SYMBOL_OPTIONS,
  TIMEFRAME_FORM_OPTIONS as TIMEFRAME_OPTIONS,
  INDICATOR_FLAT_LIST as INDICATOR_OPTIONS,
  INDICATOR_LABELS,
  CONDITION_OPTIONS,
  LEVERAGE_OPTIONS,
  DIRECTION_OPTIONS,
  REQUIREMENTS_DEFAULTS,
  OBJECTIVE_OPTIONS,
} from "@/lib/constants";
import type { SignalGene, TimeframeLayerModel, DNA, RequirementsConfig } from "@/types/api";

interface LayerData {
  timeframe: string;
  conditions: Array<{
    indicator: string;
    conditionType: string;
    value: string;
  }>;
}

interface SeedConfigFormProps {
  disabled: boolean;
  isPending: boolean;
  symbolOptions?: { value: string; label: string }[];
  onSubmit: (config: {
    symbol: string;
    initialDna: DNA;
    populationSize: number;
    maxGenerations: number;
    leverage: number;
    direction: "long" | "short" | "mixed";
    requirements?: RequirementsConfig;
  }) => void;
  seedDna?: DNA | null;
}

function inferRole(
  layers: LayerData[],
  index: number
): string | null {
  if (layers.length <= 1) return null;

  const tfOrder = ["1d", "4h", "1h", "15m"];
  const sorted = [...layers].sort(
    (a, b) => tfOrder.indexOf(a.timeframe) - tfOrder.indexOf(b.timeframe)
  );
  const layer = layers[index];
  const sortedIdx = sorted.findIndex((l) => l === layer);

  if (sortedIdx === 0) return "趋势过滤";
  if (sortedIdx === sorted.length - 1) return "入场信号";
  return "确认信号";
}

const TF_ORDER = ["1d", "4h", "1h", "15m"];

function sortLayers(layers: LayerData[]): LayerData[] {
  return [...layers].sort(
    (a, b) => TF_ORDER.indexOf(a.timeframe) - TF_ORDER.indexOf(b.timeframe)
  );
}

export function SeedConfigForm({
  disabled,
  isPending,
  onSubmit,
  seedDna,
  symbolOptions,
}: SeedConfigFormProps) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [advOpen, setAdvOpen] = useState(false);
  const [populationSize, setPopulationSize] = useState(15);
  const [maxGenerations, setMaxGenerations] = useState(200);
  const [crossLayerLogic, setCrossLayerLogic] = useState<"AND" | "OR">("AND");
  const [stopLoss, setStopLoss] = useState(5);
  const [takeProfit, setTakeProfit] = useState(10);
  const [positionSize, setPositionSize] = useState(30);
  const [leverage, setLeverage] = useState(1);
  const [direction, setDirection] = useState<"long" | "short" | "mixed">("long");
  const [requirements, setRequirements] = useState<RequirementsConfig>({
    objective: REQUIREMENTS_DEFAULTS.objective,
    min_annual_return: REQUIREMENTS_DEFAULTS.min_annual_return,
    max_drawdown: REQUIREMENTS_DEFAULTS.max_drawdown,
    min_win_rate: REQUIREMENTS_DEFAULTS.min_win_rate,
    min_total_trades: REQUIREMENTS_DEFAULTS.min_total_trades,
    min_profit_factor: REQUIREMENTS_DEFAULTS.min_profit_factor,
  });

  const initialLayers = useMemo((): LayerData[] => {
    if (seedDna?.layers && seedDna.layers.length > 0) {
      return seedDna.layers.map((layer) => ({
        timeframe: layer.timeframe,
        conditions: layer.signal_genes.map((sg) => ({
          indicator: sg.indicator,
          conditionType: sg.condition.type,
          value: sg.condition.threshold?.toString() ?? "",
        })),
      }));
    }
    return [
      {
        timeframe: "4h",
        conditions: [{ indicator: "RSI", conditionType: "lt", value: "30" }],
      },
    ];
  }, [seedDna]);

  const [layers, setLayers] = useState<LayerData[]>(initialLayers);

  // Reset layers when seedDna changes
  const prevSeedRef = useRef(seedDna);
  useEffect(() => {
    if (prevSeedRef.current === seedDna) return;
    prevSeedRef.current = seedDna;
    if (seedDna?.layers && seedDna.layers.length > 0) {
      setLayers(
        seedDna.layers.map((layer) => ({
          timeframe: layer.timeframe,
          conditions: layer.signal_genes.map((sg) => ({
            indicator: sg.indicator,
            conditionType: sg.condition.type,
            value: sg.condition.threshold?.toString() ?? "",
          })),
        }))
      );
      if (seedDna.risk_genes) {
        setStopLoss(Math.round(seedDna.risk_genes.stop_loss * 100));
        setTakeProfit(
          seedDna.risk_genes.take_profit != null
            ? Math.round(seedDna.risk_genes.take_profit * 100)
            : 10
        );
        setPositionSize(Math.round(seedDna.risk_genes.position_size * 100));
        setLeverage(seedDna.risk_genes.leverage ?? 1);
        setDirection(seedDna.risk_genes.direction ?? "long");
      }
      if (seedDna.cross_layer_logic) {
        setCrossLayerLogic(seedDna.cross_layer_logic);
      }
    }
  }, [seedDna]);

  const sortedLayers = useMemo(() => sortLayers(layers), [layers]);

  const addLayer = useCallback(() => {
    if (layers.length >= 3) return;
    const usedTfs = layers.map((l) => l.timeframe);
    const available = TIMEFRAME_OPTIONS.find(
      (t) => !usedTfs.includes(t.value)
    );
    if (!available) return;
    setLayers((prev) => [
      ...prev,
      { timeframe: available.value, conditions: [] },
    ]);
  }, [layers]);

  const removeLayer = useCallback(
    (index: number) => {
      if (layers.length <= 1) return;
      setLayers((prev) => prev.filter((_, i) => i !== index));
    },
    [layers]
  );

  const updateLayerTimeframe = useCallback(
    (index: number, tf: string) => {
      setLayers((prev) => {
        const next = [...prev];
        next[index] = { ...next[index], timeframe: tf };
        return next;
      });
    },
    []
  );

  const addCondition = useCallback((layerIdx: number) => {
    setLayers((prev) => {
      const next = [...prev];
      next[layerIdx] = {
        ...next[layerIdx],
        conditions: [
          ...next[layerIdx].conditions,
          { indicator: "EMA", conditionType: "gt", value: "" },
        ],
      };
      return next;
    });
  }, []);

  const removeCondition = useCallback(
    (layerIdx: number, condIdx: number) => {
      setLayers((prev) => {
        const next = [...prev];
        next[layerIdx] = {
          ...next[layerIdx],
          conditions: next[layerIdx].conditions.filter(
            (_, i) => i !== condIdx
          ),
        };
        return next;
      });
    },
    []
  );

  const updateCondition = useCallback(
    (
      layerIdx: number,
      condIdx: number,
      field: "indicator" | "conditionType" | "value",
      val: string
    ) => {
      setLayers((prev) => {
        const next = [...prev];
        const conds = [...next[layerIdx].conditions];
        conds[condIdx] = { ...conds[condIdx], [field]: val };
        next[layerIdx] = { ...next[layerIdx], conditions: conds };
        return next;
      });
    },
    []
  );

  const hasConditions = sortedLayers.some((l) => l.conditions.length > 0);
  const canSubmit = hasConditions && !disabled;

  const handleSubmit = useCallback(() => {
    if (!canSubmit) return;

    const tfLayers: TimeframeLayerModel[] = sortedLayers.map((layer) => ({
      timeframe: layer.timeframe,
      signal_genes: layer.conditions.map(
        (cond): SignalGene => ({
          indicator: cond.indicator,
          params: {},
          role: "entry_trigger",
          condition: {
            type: cond.conditionType as SignalGene["condition"]["type"],
            ...(cond.value ? { value: Number(cond.value) } : {}),
          },
        })
      ),
      logic_genes: { entry_logic: "AND", exit_logic: "AND" },
    }));

    const shortestTf = sortedLayers[sortedLayers.length - 1]?.timeframe ?? "4h";

    const dna: DNA = {
      layers: tfLayers,
      cross_layer_logic: crossLayerLogic,
      execution_genes: { timeframe: shortestTf, symbol },
      risk_genes: {
        stop_loss: stopLoss / 100,
        take_profit: takeProfit / 100,
        position_size: positionSize / 100,
        leverage,
        direction,
      },
      signal_genes: [],
      logic_genes: { entry_logic: "AND", exit_logic: "AND" },
      generation: 0,
      parent_ids: [],
      mutation_ops: [],
    };

    onSubmit({
      symbol,
      initialDna: dna,
      populationSize,
      maxGenerations,
      leverage,
      direction,
      requirements,
    });
  }, [
    canSubmit,
    onSubmit,
    sortedLayers,
    crossLayerLogic,
    symbol,
    stopLoss,
    takeProfit,
    positionSize,
    leverage,
    direction,
    populationSize,
    maxGenerations,
    requirements,
  ]);

  return (
    <div className="flex flex-col gap-4">
      {/* Data source */}
      <div className="flex items-center gap-3">
        <span className="w-14 shrink-0 text-xs text-slate-400">数据源</span>
        <AntSelect
          size="small"
          value={symbol}
          onChange={setSymbol}
          className="!w-28 !text-xs"
          aria-label="数据源"
          options={(symbolOptions ?? SYMBOL_OPTIONS).map((opt) => ({ value: opt.value, label: opt.label }))}
        />
      </div>

      {/* MTF Layer builder */}
      <div className="flex flex-col gap-2">
        <span className="text-xs text-slate-400">
          策略结构 (按周期分层, 每层定义该周期的信号条件)
        </span>

        {sortedLayers.map((layer, layerIdx) => {
          const originalIdx = layers.indexOf(layer);
          const role = inferRole(sortedLayers, layerIdx);
          return (
            <div
              key={originalIdx}
              className="flex flex-col gap-2 rounded-xl border border-slate-700/50 bg-white/[0.02] p-3"
            >
              {/* Layer header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-slate-300">
                    Layer {layerIdx + 1}:
                  </span>
                  <AntSelect
                    size="small"
                    value={layer.timeframe}
                    onChange={(v) => updateLayerTimeframe(originalIdx, v)}
                    className="!w-16 !text-xs"
                    aria-label={`Layer ${layerIdx + 1} 周期`}
                    options={TIMEFRAME_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
                  />
                  {role && (
                    <Badge className="!m-0 !text-[10px] !text-slate-500">{role}</Badge>
                  )}
                </div>
                {layers.length > 1 && (
                  <Button
                                        size="small"
                    className="text-slate-600 hover:text-red-400"
                    onClick={() => removeLayer(originalIdx)}
                    aria-label="删除层"
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>

              {/* Conditions */}
              {layer.conditions.map((cond, condIdx) => (
                <div
                  key={condIdx}
                  className="flex items-center gap-2 pl-2"
                >
                  <AntSelect
                    size="small"
                    value={cond.indicator}
                    onChange={(v) => updateCondition(originalIdx, condIdx, "indicator", v)}
                    className="!w-24 !text-xs"
                    aria-label="条件指标"
                    options={INDICATOR_OPTIONS.map((ind) => ({ value: ind, label: INDICATOR_LABELS[ind] ?? ind }))}
                  />

                  <AntSelect
                    size="small"
                    value={cond.conditionType}
                    onChange={(v) => updateCondition(originalIdx, condIdx, "conditionType", v)}
                    className="!w-24 !text-xs"
                    aria-label="条件类型"
                    options={CONDITION_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
                  />

                  {![
                    "price_above",
                    "price_below",
                    "cross_above",
                    "cross_below",
                  ].includes(cond.conditionType) && (
                    <Input
                      size="small"
                      value={cond.value}
                      onChange={(e) => updateCondition(originalIdx, condIdx, "value", e.target.value)}
                      className="!w-20 !text-xs"
                      placeholder="阈值"
                      aria-label="条件阈值"
                    />
                  )}

                  <Button
                                        size="small"
                    className="text-slate-600 hover:text-red-400"
                    onClick={() => removeCondition(originalIdx, condIdx)}
                    aria-label="删除条件"
                  >
                    <X className="h-3 w-3.5" />
                  </Button>
                </div>
              ))}

              <Button
                                size="small"
                className="w-fit gap-1 text-[11px] text-slate-500 hover:text-slate-400"
                onClick={() => addCondition(originalIdx)}
              >
                <Plus className="h-3 w-3" />
                添加条件
              </Button>
            </div>
          );
        })}

        {layers.length < 3 && (
          <Button
           
            size="small"
            className="w-full gap-1 border-dashed border-slate-700/50 text-[11px] text-slate-500"
            onClick={addLayer}
          >
            <Plus className="h-3 w-3" />
            添加周期层
          </Button>
        )}

        {layers.length > 1 && (
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span>跨层组合:</span>
            <AntSelect
              size="small"
              value={crossLayerLogic}
              onChange={(v) => setCrossLayerLogic(v as "AND" | "OR")}
              className="!w-16 !text-xs"
              aria-label="跨层组合逻辑"
              options={[
                { value: "AND", label: "AND" },
                { value: "OR", label: "OR" },
              ]}
            />
            <span className="text-slate-600">
              {crossLayerLogic === "AND"
                ? "所有层信号同时满足才入场"
                : "任一层信号即可入场"}
            </span>
          </div>
        )}
      </div>

      {/* Risk params */}
      <div className="flex items-center gap-4">
        <span className="w-14 shrink-0 text-xs text-slate-400">风控</span>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">止损</span>
          <InputNumber
            step={1}
            min={1}
            max={20}
            size="small"
            value={stopLoss}
            onChange={(v) => setStopLoss(v ?? 5)}
            className="!w-16 !text-xs"
            aria-label="止损百分比"
          />
          <span className="text-[11px] text-slate-600">%</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">止盈</span>
          <InputNumber
            step={1}
            min={1}
            max={50}
            size="small"
            value={takeProfit}
            onChange={(v) => setTakeProfit(v ?? 10)}
            className="!w-16 !text-xs"
            aria-label="止盈百分比"
          />
          <span className="text-[11px] text-slate-600">%</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">仓位</span>
          <InputNumber
            step={1}
            min={10}
            max={100}
            size="small"
            value={positionSize}
            onChange={(v) => setPositionSize(v ?? 30)}
            className="!w-16 !text-xs"
            aria-label="仓位百分比"
          />
          <span className="text-[11px] text-slate-600">%</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">杠杆</span>
          <AntSelect
            size="small"
            value={String(leverage)}
            onChange={(v) => setLeverage(Number(v))}
            className="!w-16 !text-xs"
            aria-label="杠杆"
            options={LEVERAGE_OPTIONS.map((opt) => ({ value: String(opt.value), label: opt.label }))}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">方向</span>
          <AntSelect
            size="small"
            value={direction}
            onChange={(v) => setDirection(v as "long" | "short" | "mixed")}
            className="!w-16 !text-xs"
            aria-label="方向约束"
            options={DIRECTION_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
          />
        </div>
      </div>
      {leverage > 1 && (
        <p className="text-[11px] text-amber-500/80">
          {leverage}x 杠杆: 每 8 小时收取 0.1% 资金费用, 保证金亏损超过 90% 触发爆仓
        </p>
      )}

      {/* Running requirements (directly visible) */}
      <div className="flex items-start gap-3">
        <span className="mt-1 w-14 shrink-0 text-xs text-slate-400">
          运行要求
        </span>
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-500">优化目标</span>
            <AntSelect
              size="small"
              value={requirements.objective ?? "sharpe"}
              onChange={(v) => setRequirements((prev) => ({ ...prev, objective: v }))}
              className="!w-28 !text-xs"
              aria-label="优化目标"
              options={OBJECTIVE_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
            />
          </div>
          <div className="grid grid-cols-5 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">年化收益 %</label>
              <InputNumber
                min={0}
                max={1000}
                step={1}
                size="small"
                value={requirements.min_annual_return * 100}
                onChange={(v) =>
                  setRequirements((prev) => ({
                    ...prev,
                    min_annual_return: (v ?? 0) / 100 || 0,
                  }))
                }
                className="!w-full !text-xs"
                aria-label="年化收益百分比"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">最大回撤 %</label>
              <InputNumber
                min={0}
                max={100}
                step={1}
                size="small"
                value={requirements.max_drawdown * 100}
                onChange={(v) =>
                  setRequirements((prev) => ({
                    ...prev,
                    max_drawdown: (v ?? 0) / 100 || 0,
                  }))
                }
                className="!w-full !text-xs"
                aria-label="最大回撤百分比"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">最低胜率 %</label>
              <InputNumber
                min={0}
                max={100}
                step={1}
                size="small"
                value={requirements.min_win_rate * 100}
                onChange={(v) =>
                  setRequirements((prev) => ({
                    ...prev,
                    min_win_rate: (v ?? 0) / 100 || 0,
                  }))
                }
                className="!w-full !text-xs"
                aria-label="最低胜率百分比"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">最少交易</label>
              <InputNumber
                min={0}
                max={1000}
                step={1}
                size="small"
                value={requirements.min_total_trades}
                onChange={(v) => setRequirements((prev) => ({ ...prev, min_total_trades: v ?? 0 }))}
                className="!w-full !text-xs"
                aria-label="最少交易次数"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">盈亏比</label>
              <InputNumber
                min={0}
                max={10}
                step={0.1}
                size="small"
                value={requirements.min_profit_factor}
                onChange={(v) => setRequirements((prev) => ({ ...prev, min_profit_factor: v ?? 0 }))}
                className="!w-full !text-xs"
                aria-label="盈亏比下限"
              />
            </div>
          </div>
          <span className="text-[10px] text-slate-600">
            至少配置收益和回撤两项, 满足所有要求的策略将被标记为达标
          </span>
        </div>
      </div>

      {/* Note */}
      <p className="text-[11px] leading-relaxed text-slate-600">
        这些条件作为进化的起点(种子), 进化会在其基础上变异优化.
        包括: 调整参数/替换指标/改变周期/增减条件/增减周期层
      </p>

      {/* Advanced params (collapsible) - only population size + max generations */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-400"
          onClick={() => setAdvOpen((v) => !v)}
        >
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 transition-transform",
              advOpen && "rotate-180"
            )}
          />
          高级参数
        </button>
        {advOpen && (
          <div className="flex items-center gap-4 rounded-lg border border-slate-700/30 bg-white/[0.02] p-3">
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-slate-500">
                种群大小 (10-50)
              </label>
              <InputNumber
                min={10}
                max={50}
                size="small"
                value={populationSize}
                onChange={(v) => setPopulationSize(v ?? 15)}
                className="!w-24 !text-xs"
                aria-label="种群大小"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-slate-500">
                最大代数 (50-500)
              </label>
              <InputNumber
                min={50}
                max={500}
                size="small"
                value={maxGenerations}
                onChange={(v) => setMaxGenerations(v ?? 200)}
                className="!w-24 !text-xs"
                aria-label="最大代数"
              />
            </div>
          </div>
        )}
      </div>

      {/* Submit */}
      <Button
        size="small"
        className="w-full gap-1.5 bg-amber-400 text-black hover:bg-amber-400/90"
        disabled={!canSubmit || isPending}
        onClick={handleSubmit}
      >
        <Play className="h-3.5 w-3.5" />
        {isPending ? "创建中..." : "开始探索"}
      </Button>
    </div>
  );
}
