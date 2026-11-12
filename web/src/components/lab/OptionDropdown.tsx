import { useState, useMemo, useRef } from "react";
import { Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { DropdownPortal } from "./DropdownPortal";

export interface OptionItem {
  value: string;
  label: string;
  category: string;
}

interface OptionDropdownProps {
  /** 当前选中值。 */
  value: string;
  /** 触发按钮上显示的标签（调用方解析，如 getSubjectLabel）。 */
  label: string;
  /** 扁平选项表；渲染时按 category 分组。 */
  options: OptionItem[];
  onChange: (value: string) => void;
  /** 是否显示搜索框。SubjectDropdown 用，ActionDropdown 不用。 */
  searchable?: boolean;
  /** DropdownPortal 宽度。 */
  width?: number;
  /** 选项表的 Tailwind max-height class，如 "max-h-56"。 */
  maxHeightClass?: string;
  searchPlaceholder?: string;
}

/**
 * SubjectDropdown 与 ActionDropdown 共用的「触发按钮+portal+分组选项表」
 * 下拉——收敛触发样式、portal 接线与分组渲染的重复；真实差异
 * （可搜索/宽度/最大高/选项源）全部参数化。
 */
export function OptionDropdown({
  value,
  label,
  options,
  onChange,
  searchable = false,
  width = 176,
  maxHeightClass = "max-h-56",
  searchPlaceholder = "搜索...",
}: OptionDropdownProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);

  const categories = useMemo(
    () => [...new Set(options.map((o) => o.category))],
    [options],
  );
  const filtered = useMemo(() => {
    if (!searchable || !search) return options;
    const q = search.toLowerCase();
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q),
    );
  }, [options, search, searchable]);

  const handleClose = () => {
    setOpen(false);
    setSearch("");
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={cn(
          "h-7 rounded-md border px-2 text-xs transition-colors",
          "border-border-default bg-bg-surface text-text-primary",
          "hover:border-accent-gold/50",
        )}
        onClick={() => setOpen(!open)}
      >
        {label}
      </button>

      <DropdownPortal triggerRef={triggerRef} open={open} onClose={handleClose} width={width}>
        <div className="rounded-lg border border-border-default bg-bg-surface shadow-xl">
          {searchable && (
            <div className="flex items-center gap-2 border-b border-border-default px-2 py-1.5">
              <Search className="h-3 w-3 text-text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={searchPlaceholder}
                className="w-full bg-transparent text-xs text-text-primary outline-none placeholder:text-text-muted"
                autoFocus
              />
            </div>
          )}
          <div className={cn("overflow-y-auto py-1", maxHeightClass)}>
            {categories.map((cat) => {
              const items = filtered.filter((o) => o.category === cat);
              if (items.length === 0) return null;
              return (
                <div key={cat}>
                  <div className="px-2 py-1 text-[10px] font-semibold text-text-muted">
                    {cat}
                  </div>
                  {items.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      className={cn(
                        "flex w-full items-center px-3 py-1.5 text-xs transition-colors",
                        value === opt.value
                          ? "bg-accent-gold/10 text-accent-gold"
                          : "text-text-primary hover:bg-white/5",
                      )}
                      onClick={() => {
                        onChange(opt.value);
                        handleClose();
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </DropdownPortal>
    </>
  );
}
