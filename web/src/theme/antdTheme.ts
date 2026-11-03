import { theme as antdTheme, type ThemeConfig } from "antd";

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
    colorBgBase: "#0D0F24" /* BG.bg0 */,
    colorBgContainer: "#151735" /* BG.bg1 预混合：#20224E66 叠 #0D0F24 */,
    colorBgElevated: "#1E1F47" /* BG.bg2 预混合：#2A2A5E99 叠 #0D0F24 */,
    // 边框三态（原 --color-border-default/hover）
    colorBorder: "rgba(255, 255, 255, 0.10)" /* BORDER.default=#FFFFFF1A */,
    colorBorderSecondary: "rgba(255, 255, 255, 0.08)" /* BORDER.line=#FFFFFF14 */,
    // 文本三级（原 --color-text-primary/secondary/muted）
    colorText: "#F4F2FF" /* TEXT.primary */,
    colorTextSecondary: "#A8A3C4" /* TEXT.secondary */,
    colorTextTertiary: "#78739B" /* TEXT.tertiary */,
    // 语义色（原 --color-accent-gold/profit/loss/warning/info）
    colorPrimary: "#FFC76B" /* ATTENTION.gold */,
    colorSuccess: "#2DD4BF" /* POLARITY.up */,
    colorError: "#FB7185" /* POLARITY.down */,
    colorWarning: "#FCD34D" /* STATE.warn */,
    colorInfo: "#8B5CF6",
    // 圆角（原 --radius-md 主档）
    borderRadius: 8,
  },
};
