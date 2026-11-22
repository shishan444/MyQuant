/**
 * 验证记录页（路由 /verify）。记录浏览壳：头部+浏览骨架主体
 * （筛选→卡片流→明细——RecordsBrowser 组合）。发起器与历史会话
 * 视图随 verify 会话体系退役删除（fix-replay-gap-alignment U8——
 * 记录本体走 validationRecords 端点，浏览与发起解耦）。
 * 消费方：App 路由（页面入口）。
 */
import { ShieldCheck } from "lucide-react";
import { PageTransition } from "@/components/PageTransition";
import { RecordsBrowser } from "@/components/validationRecords/RecordBrowser";

export function Verify() {
  return (
    <PageTransition>
      <div className="p-4 space-y-3">
        {/* 头部（fe-validation-records-page：浏览为主水体） */}
        <div className="flex items-center gap-2.5">
          <ShieldCheck className="h-4 w-4 text-blue-400" />
          <h1 className="text-sm font-semibold text-text-primary">验证记录</h1>
        </div>

        {/* 浏览骨架主体：筛选→卡片流（独立滚动）→明细（常驻）——
            upgrade-validation-records-list 5.1 L6 左右分栏 */}
        <RecordsBrowser />
      </div>
    </PageTransition>
  );
}
