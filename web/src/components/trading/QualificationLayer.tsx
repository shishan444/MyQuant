import { Tag } from "antd";
import type { TradingReportQualification } from "@/types/api";

/**
 * 结题报告达标判定层（paper-trading-revamp 组 F3——07-B6 报告第四段消费）。
 * trigger_consistency.passed 三态呈现（NULL≠false≠true——NULL=对照重放
 * 不可执行的降级态，如实披露不折算）；transition 段凭证迁移结果披露。
 */
export function QualificationLayer({ qualification }: { qualification?: TradingReportQualification }) {
  if (!qualification) {
    return (
      <p className="text-xs text-text-muted">
        旧版报告无达标判定段（此报告生成于判定器上线前）。
      </p>
    );
  }
  const tc = qualification.trigger_consistency;
  const consistencyTag =
    tc.passed === null ? (
      <Tag color="default">不可判（NULL）</Tag>
    ) : tc.passed ? (
      <Tag color="success">通过</Tag>
    ) : (
      <Tag color="error">未通过</Tag>
    );
  const tr = qualification.transition;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="text-text-secondary">
          收益转正 {qualification.pnl_positive ? "✓" : "✗"}
        </span>
        <span className="text-text-secondary">
          笔数≥5 {qualification.trades_enough ? "✓" : "✗"}
        </span>
        <span className="text-text-secondary">
          触发一致性 {consistencyTag}
          {tc.deviation_ratio !== null && (
            <span className="font-mono ml-1">
              差异率 {tc.deviation_ratio.toFixed(4)}（{tc.numerator}/{tc.denominator}）
            </span>
          )}
        </span>
        <Tag color={qualification.qualified ? "gold" : "default"}>
          {qualification.qualified ? "达标（qualified）" : "未达标"}
        </Tag>
      </div>
      {tc.passed === null && tc.detail && "reason" in tc.detail && (
        <p className="text-[11px] text-text-muted">
          降级原因：{String((tc.detail as { reason: unknown }).reason)}
        </p>
      )}
      {tr?.attempted && (
        <p className="text-[11px] text-text-muted">
          凭证迁移：{tr.migrated ? "已迁 candidate" : `未迁移（${tr.not_migrated_reason}）`}
        </p>
      )}
      <p className="text-[11px] text-text-muted">
        判据：收益&gt;0 严格 AND 笔数≥5 含等 AND 触发差异≤10% 含等（回撤披露不计入）；
        判定时点 {qualification.judged_at ?? "—"}
      </p>
    </div>
  );
}
