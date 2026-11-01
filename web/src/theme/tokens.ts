/**
 * 目标态设计令牌（global-nav-shell 从 pencil 原型画布核取，2026-10-31）。
 * 权威源=画布变量表（pencil-new.pen GetVariables），29 项；色值逐字对齐画布。
 *
 * 定位（G14 形制先例——ui-uniformity「图表色经集中常量定义」）：
 * 多态语义族（state 八态/polarity 极性对/live 实心对）不进 AntD ThemeConfig 单值槽，
 * 以 TS 集中常量为唯一权威，自定义组件与后续页面（C2-C8）直接消费；
 * 既有 index.css @theme 过渡期 token 与 antdTheme.ts 既有值不动（「只补不删不改」，
 * 旧系迁移归后续 ui-uniformity 基座收口）。
 */

/** 背景三层（画布深紫夜底系） */
export const BG = {
  bg0: "#0D0F24",
  bg1: "#20224E66",
  bg2: "#2A2A5E99",
} as const;

/** 边框五档（同族白透明四档+金选中档）。default=画布实证 #FFFFFF1A（0.10）；旧载体两侧 index.css --color-border-default 与 antdTheme colorBorder 均 0.08，按「只补不删不改」不动，微差在案待基座收口 */
export const BORDER = {
  faint: "#FFFFFF0E",
  line: "#FFFFFF14",
  default: "#FFFFFF1A",
  strong: "#FFFFFF2E",
  selected: "#FFC76B99",
} as const;

/** 文本三级（画布紫灰系；@theme 同名 text-primary/secondary 为旧色系值，目标态组件以本表为准） */
export const TEXT = {
  primary: "#F4F2FF",
  secondary: "#A8A3C4",
  tertiary: "#78739B",
} as const;

/** 数值极性域（画布推荐双主张：青涨/玫跌——待用户复核后全局切换，旧 profit/loss 不动） */
export const POLARITY = {
  up: "#2DD4BF",
  down: "#FB7185",
} as const;

/** 状态徽标域八态（G-1 描边色；live 例外走实心对） */
export const STATE = {
  running: "#5EEAD4",
  ok: "#2DD4BF",
  error: "#FDA4AF",
  pending: "#A8A3C4",
  stopped: "#78739B",
  warn: "#FCD34D",
  draft: "#78739B",
  archived: "#565270",
} as const;

/** 注意力金族（金=需要你的注意力） */
export const ATTENTION = {
  gold: "#FFC76B",
} as const;

/** 纸盘域紫（07 页纸盘标识域） */
export const DOMAIN = {
  paper: "#A78BFA",
} as const;

/** live 唯一实心对（白底黑字——G-1 唯一实心例外） */
export const LIVE_BADGE = {
  bg: "#F4F2FF",
  text: "#160F2E",
} as const;

/** 字体两族（Inter 正文/Geist Mono 数字——与 @theme --font-sans/--font-mono 同源） */
export const FONT = {
  body: "Inter",
  num: "Geist Mono",
} as const;

/** 圆角两档 */
export const RADIUS = {
  main: 8,
  badge: 4,
} as const;

/** 徽标状态类型全集（八态+live 实心例外） */
export type BadgeStatus = keyof typeof STATE | "live";
