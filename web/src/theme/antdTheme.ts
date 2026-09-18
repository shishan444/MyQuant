import { theme as antdTheme, type ThemeConfig } from "antd";

/**
 * AntD theme token 唯一来源（G4 色彩映射表落地）。
 * 语义色承接自原 @theme 品牌组（视觉语义唯一来源=AntD theme token——ui-uniformity spec）：
 * 金=品牌主色、profit/loss 语义色不变仅载体变；图表色不入 token（G14 集中常量）；
 * 原 rgba 毛玻璃归 GlassCard 业务层，容器色用 solid 近似。
 */
export const antdThemeConfig: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    // 背景层次（原 --color-bg-base/surface/elevated）
    colorBgBase: "#0a0a0f",
    colorBgContainer: "#161b22",
    colorBgElevated: "#1e242c",
    // 边框三态（原 --color-border-default/hover）
    colorBorder: "rgba(255, 255, 255, 0.08)",
    colorBorderSecondary: "rgba(255, 255, 255, 0.15)",
    // 文本三级（原 --color-text-primary/secondary/muted）
    colorText: "#f8fafc",
    colorTextSecondary: "#94a3b8",
    colorTextTertiary: "#484f58",
    // 语义色（原 --color-accent-gold/profit/loss/warning/info）
    colorPrimary: "#F59E0B",
    colorSuccess: "#00C853",
    colorError: "#EF4444",
    colorWarning: "#FBBF24",
    colorInfo: "#8B5CF6",
    // 圆角（原 --radius-md 主档）
    borderRadius: 8,
  },
};
