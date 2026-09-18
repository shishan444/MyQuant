import { create } from "zustand";
import { persist } from "zustand/middleware";
import { CHART_INDICATOR_DEFAULTS, EMA_DEFAULT_COLORS } from "@/lib/constants";
import type { ChartIndicatorConfig } from "@/types/api";

interface EmaEntry {
  period: number;
  color: string;
  enabled: boolean;
}

interface BollConfig {
  enabled: boolean;
  period: number;
  std: number;
  color: string;
}

interface RsiConfig {
  enabled: boolean;
  period: number;
  overbought: number;
  oversold: number;
}

interface VolConfig {
  enabled: boolean;
  position: "overlay" | "separate";
}

interface ChartSettingsState {
  emaList: EmaEntry[];
  boll: BollConfig;
  rsi: RsiConfig;
  vol: VolConfig;

  // EMA actions
  addEma: (period: number) => void;
  removeEma: (index: number) => void;
  updateEma: (index: number, updates: Partial<EmaEntry>) => void;
  reorderEma: (fromIndex: number, toIndex: number) => void;

  // BOLL actions
  setBoll: (updates: Partial<BollConfig>) => void;

  // RSI actions
  setRsi: (updates: Partial<RsiConfig>) => void;

  // VOL actions
  setVol: (updates: Partial<VolConfig>) => void;

  // Reset
  resetToDefaults: () => void;

  // Derived helpers
  getEmaPeriods: () => number[];
  getBollParams: () => { period: number; std: number };
  getIndicatorParams: () => ChartIndicatorConfig;
}

function buildDefaultEmaList(): EmaEntry[] {
  return CHART_INDICATOR_DEFAULTS.ema_periods.map((period, i) => ({
    period,
    color: EMA_DEFAULT_COLORS[i % EMA_DEFAULT_COLORS.length],
    enabled: true,
  }));
}

export const useChartSettings = create<ChartSettingsState>()(
  persist(
    (set, get) => ({
      emaList: buildDefaultEmaList(),
      boll: { ...CHART_INDICATOR_DEFAULTS.boll },
      rsi: { ...CHART_INDICATOR_DEFAULTS.rsi },
      vol: { ...CHART_INDICATOR_DEFAULTS.vol },

      addEma: (period) =>
        set((s) => {
          const colorIndex = s.emaList.length % EMA_DEFAULT_COLORS.length;
          return {
            emaList: [
              ...s.emaList,
              { period, color: EMA_DEFAULT_COLORS[colorIndex], enabled: true },
            ],
          };
        }),

      removeEma: (index) =>
        set((s) => ({
          emaList: s.emaList.filter((_, i) => i !== index),
        })),

      updateEma: (index, updates) =>
        set((s) => ({
          emaList: s.emaList.map((entry, i) =>
            i === index ? { ...entry, ...updates } : entry,
          ),
        })),

      reorderEma: (fromIndex, toIndex) =>
        set((s) => {
          const list = [...s.emaList];
          const [moved] = list.splice(fromIndex, 1);
          list.splice(toIndex, 0, moved);
          return { emaList: list };
        }),

      setBoll: (updates) =>
        set((s) => ({ boll: { ...s.boll, ...updates } })),

      setRsi: (updates) =>
        set((s) => ({ rsi: { ...s.rsi, ...updates } })),

      setVol: (updates) =>
        set((s) => ({ vol: { ...s.vol, ...updates } })),

      resetToDefaults: () =>
        set({
          emaList: buildDefaultEmaList(),
          boll: { ...CHART_INDICATOR_DEFAULTS.boll },
          rsi: { ...CHART_INDICATOR_DEFAULTS.rsi },
          vol: { ...CHART_INDICATOR_DEFAULTS.vol },
        }),

      getEmaPeriods: () => get().emaList.filter((e) => e.enabled).map((e) => e.period),

      getBollParams: () => {
        const b = get().boll;
        return { period: b.period, std: b.std };
      },

      getIndicatorParams: () => {
        const s = get();
        const enabledEma = s.emaList.filter((e) => e.enabled);
        return {
          ema_periods: enabledEma.map((e) => e.period),
          ema_colors: enabledEma.map((e) => e.color),
          boll: { ...s.boll },
          rsi: { ...s.rsi },
          vol: { ...s.vol },
        };
      },
    }),
    {
      name: "chart-indicator-settings",
      version: 1,
      // 状态形态自 G4 冻结后未变；后续增删字段时在此按 version 写 migrate
      // （G17.1 持久化审计落档）
    },
  ),
);
