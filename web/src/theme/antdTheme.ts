import { theme as antdTheme, type ThemeConfig } from "antd";
import {
  BG,
  BORDER,
  TEXT,
  POLARITY,
  STATE,
  ATTENTION,
} from "@/theme/tokens";

/**
 * AntD theme token 受控从源（fe-shell-theme-cutover T1）。
 * 唯一权威=web/src/theme/tokens.ts；本文件槽位逐项与 tokens 同值（映射守卫测试机器判定，
 * 同名异值即违规）。bg1/bg2 为 tokens 半透明值叠于 bg0 的预混合实色（alpha 混合公式）。
 * colorSuccess/Error 红→teal/rose 与 colorPrimary 金变为 T2 有意变化（POLARITY/ATTENTION 语义）。
 * 图表色不入 token（G14 集中常量 chartThemes）；毛玻璃归 GlassCard 业务层，容器色用实色近似。
 */
export const antdThemeConfig: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    // 背景层次（原 --color-bg-base/surface/elevated）
    colorBgBase: BG.bg0,
    colorBgContainer: "#151735" /* BG.bg1 预混合实色（半透明不可直引） */,
    colorBgElevated: "#1E1F47" /* BG.bg2 预混合实色 */,
    // 边框三态（原 --color-border-default/hover）
    colorBorder: BORDER.default /* hex8 直引——与 tokens 引用等值 */,
    colorBorderSecondary: BORDER.line,
    // 文本三级（原 --color-text-primary/secondary/muted）
    colorText: TEXT.primary,
    colorTextSecondary: TEXT.secondary,
    colorTextTertiary: TEXT.tertiary,
    // 语义色（原 --color-accent-gold/profit/loss/warning/info）
    colorPrimary: ATTENTION.gold,
    colorSuccess: POLARITY.up,
    colorError: POLARITY.down,
    colorWarning: STATE.warn,
    colorInfo: "#8B5CF6",
    // 圆角（原 --radius-md 主档）
    borderRadius: 8,
  },
};
