/**
 * SymbolInfoBar 特征测试（kline-common-layer 组 2）——交易对信息栏现状记录语义。
 * 数据面=诚实子集：最新价/装载窗聚合（窗=已装载 candles 全体）现算，
 * 累计资金费直读任务字段；空数据态=「行情装载中…」（组件注释契约）。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { SymbolInfoBar } from "@/components/trading/SymbolInfoBar";

const UP_CANDLES = [
  { timestamp: "2026-01-01 00:00:00", open: 100, high: 110, low: 95, close: 105, volume: 10 },
  { timestamp: "2026-01-01 04:00:00", open: 105, high: 115, low: 100, close: 112, volume: 20 },
];

function lastPriceEl() {
  return screen.getByTestId("symbol-last-price");
}

describe("SymbolInfoBar 装载窗聚合与涨跌色", () => {
  it("上涨窗：最新价/涨跌幅/高/低/量聚合现算，涨幅红绿走 profit 色", () => {
    render(<SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={UP_CANDLES as never} />);
    // change=(112-100)/100=+12%；last=112；high=115；low=95；vol=30
    expect(lastPriceEl()).toHaveTextContent("112.00");
    expect(lastPriceEl().style.color).toBe("var(--color-profit)");
    expect(screen.getByText("+12.00%")).toBeInTheDocument();
    expect(screen.getByText("115.00")).toBeInTheDocument(); // 装载窗高
    expect(screen.getByText("95.00")).toBeInTheDocument(); // 装载窗低
    expect(screen.getByText("30")).toBeInTheDocument(); // 装载窗量（整数位）
  });

  it("下跌窗：change<0 走 loss 色，负号自带无显式 +", () => {
    const down = [
      { timestamp: "2026-01-01 00:00:00", open: 110, high: 120, low: 90, close: 108, volume: 5 },
      { timestamp: "2026-01-01 04:00:00", open: 108, high: 112, low: 88, close: 99, volume: 5 },
    ];
    render(<SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={down as never} />);
    // change=(99-110)/110=-10%
    expect(lastPriceEl().style.color).toBe("var(--color-loss)");
    expect(screen.getByText("-10.00%")).toBeInTheDocument();
  });

  it("change=0 边界：0>=0 归涨侧（profit 色带显式 +）", () => {
    const flat = [
      { timestamp: "2026-01-01 00:00:00", open: 100, high: 100, low: 100, close: 100, volume: 1 },
    ];
    render(<SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={flat as never} />);
    expect(lastPriceEl().style.color).toBe("var(--color-profit)");
    expect(screen.getByText("+0.00%")).toBeInTheDocument();
  });

  it("装载窗量 vol=0 不渲染量字段（高/低仍在）", () => {
    const zeroVol = UP_CANDLES.map((c) => ({ ...c, volume: 0 }));
    render(<SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={zeroVol as never} />);
    expect(screen.queryByText(/装载窗量/)).not.toBeInTheDocument();
    expect(screen.getByText("115.00")).toBeInTheDocument();
  });
});

describe("SymbolInfoBar 空数据与资金费字段", () => {
  it("空数据态：candles=[] 显示行情装载中，不渲染价格节点", () => {
    render(<SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={[]} />);
    expect(screen.getByText("行情装载中…")).toBeInTheDocument();
    expect(screen.queryByTestId("symbol-last-price")).not.toBeInTheDocument();
  });

  it("cumulativeFunding 不传/null 不渲染；0 渲染（0!=null）；负值无显式 +", () => {
    const { rerender } = render(
      <SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={UP_CANDLES as never} />,
    );
    expect(screen.queryByText(/累计资金费/)).not.toBeInTheDocument();

    rerender(
      <SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={UP_CANDLES as never} cumulativeFunding={null} />,
    );
    expect(screen.queryByText(/累计资金费/)).not.toBeInTheDocument();

    rerender(
      <SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={UP_CANDLES as never} cumulativeFunding={0} />,
    );
    expect(screen.getByText("+0.0000 USDT")).toBeInTheDocument();

    rerender(
      <SymbolInfoBar symbol="BTCUSDT" timeframe="4h" candles={UP_CANDLES as never} cumulativeFunding={-1.5} />,
    );
    expect(screen.getByText("-1.5000 USDT")).toBeInTheDocument();
  });
});
