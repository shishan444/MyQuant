import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

interface EquityPoint {
  timestamp: string;
  equity: number;
  balance: number;
}

interface EquityCurveProps {
  data: EquityPoint[];
  initialCash: number;
  height?: number;
}

function formatTime(ts: string) {
  return ts.slice(5, 16).replace("T", " ");
}

function formatMoney(v: number) {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

export function EquityCurve({ data, initialCash, height = 200 }: EquityCurveProps) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-border-default"
        style={{ height }}>
        <span className="text-sm text-text-muted">No equity data yet</span>
      </div>
    );
  }

  const isProfit = data[data.length - 1].equity >= initialCash;
  const strokeColor = isProfit ? "#10b981" : "#ef4444";

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
        <defs>
          <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={strokeColor} stopOpacity={0.3} />
            <stop offset="95%" stopColor={strokeColor} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="timestamp"
          tickFormatter={formatTime}
          tick={{ fontSize: 10, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          minTickGap={60}
        />
        <YAxis
          tickFormatter={formatMoney}
          tick={{ fontSize: 10, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          width={60}
          domain={["dataMin - 500", "dataMax + 500"]}
        />
        <Tooltip
          formatter={(value) => [formatMoney(Number(value)), "Equity"]}
          labelFormatter={(label) => formatTime(String(label))}
          contentStyle={{
            backgroundColor: "#1a1a2e",
            border: "1px solid #2a2a3e",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={strokeColor}
          strokeWidth={1.5}
          fillOpacity={1}
          fill="url(#equityGrad)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
