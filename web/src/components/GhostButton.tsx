import { Button } from "antd";
import type { ButtonProps } from "antd";
import { BORDER, TEXT } from "@/theme/tokens";

/**
 * G-3 幽灵按钮（global-nav-shell）——形制核取自原型画布 G-3 frame：
 * 无填充+border-strong 1px 描边+cornerRadius 4+fs12 次级文本色。
 * 交互基座=AntD Button（ui-uniformity 裁决：不重建交互机制）。
 */
export function GhostButton({ children, style, ...rest }: ButtonProps) {
  return (
    <Button
      type="text"
      {...rest}
      style={{
        border: `1px solid ${BORDER.strong}`,
        borderRadius: 4,
        padding: "4px 10px",
        height: "auto",
        color: TEXT.secondary,
        ...style,
      }}
    >
      {children}
    </Button>
  );
}
