/**
 * 目标态设计令牌（global-nav-shell 从 pencil 原型画布核取，2026-10-31；图表三色板 2026-11-03 追加）。
 * 权威源=画布变量表（pencil-new.pen GetVariables），46 项（主体系 29+图表三色板 17）；色值逐字对齐画布。
 *
 * 定位（G14 形制先例——ui-uniformity「图表色经集中常量定义」）：
 * 多态语义族（state 八态/polarity 极性对/live 实心对）不进 AntD ThemeConfig 单值槽，
 * 以 TS 集中常量为唯一权威，自定义组件与后续页面直接消费；
 * index.css @theme 与 antdTheme.ts 为受控从源（fe-shell-theme-cutover T1 已逐项对齐，
 * 映射守卫测试机器判定——同名异值即违规，旧系「只补不删不改」过渡期已收口）。
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

/** 图表三色板 va（琥珀终端风——05 进化图表/07 交易图列；值从原型画布提取 2026-11-03） */
export const CHART_VA = {
  bg: "#050505",
  panel: "#0D0D0F",
  line: "#26262B",
  amber: "#FFB020",
  text: "#E8E8E3",
  dim: "#7A7A80",
} as const;

/** 图表三色板 vb（蓝风——08 深空图列+B6r 决策快照） */
export const CHART_VB = {
  bg: "#0E1116",
  panel: "#161B22",
  panel2: "#1C232D",
  blue: "#5B9DFF",
  text: "#EDF0F4",
  dim: "#8B95A5",
} as const;

/** 图表三色板 vc（青风——03 验证记录 K 线复核） */
export const CHART_VC = {
  bg: "#0B0D12",
  float: "#141922CC",
  teal: "#2DD4BF",
  text: "#E6EAF2",
  dim: "#828CA0",
} as const;

/**
 * 图表三色板页面变体映射（单一权威——各页 change 按表消费不得各自发明）：
 * 05 进化图表+07 交易图列=CHART_VA；08 深空图列+B6r=CHART_VB；03 K线复核=CHART_VC；
 * 02 实验室中列=主体系紫底不消费（取色实证）；01/04/06/09/设置/插件不消费。
 * 蜡烛恒等规则：三色板只管底/面板/辅线/浮层/文字色，K 线涨跌色一律 POLARITY。
 */
export const CHART_VARIANT_MAP = {
  "05-evolution": "va",
  "07-trading": "va",
  "08-live": "vb",
  "08-b6r-decisions": "vb",
  "03-validation-review": "vc",
} as const;
