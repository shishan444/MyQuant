import { Button } from "antd";

interface EmptyStateAction {
  label: string;
  onClick: () => void;
  variant?: "default" | "outline";
}

interface EmptyStateProps {
  icon: React.ElementType;
  title: string;
  description: string;
  actions?: Array<EmptyStateAction>;
}

function EmptyState({ icon: Icon, title, description, actions }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-12">
      <Icon className="h-12 w-12 text-text-muted" />
      <div className="flex flex-col items-center gap-1.5 text-center">
        <h3 className="text-base font-medium text-text-primary">{title}</h3>
        <p className="text-sm text-text-secondary">{description}</p>
      </div>
      {actions && actions.length > 0 && (
        <div className="flex items-center gap-3">
          {actions.map((action) => (
            <Button
              key={action.label}
              type={action.variant === "outline" ? "default" : "primary"}
              onClick={action.onClick}
            >
              {action.label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

export { EmptyState };
export type { EmptyStateProps, EmptyStateAction };
