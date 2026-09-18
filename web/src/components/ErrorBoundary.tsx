import { Component, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "antd";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  retryKey: number;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, retryKey: 0 };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, retryKey: 0 };
  }

  handleRetry = () => {
    this.setState((prev) => ({
      hasError: false,
      error: null,
      retryKey: prev.retryKey + 1,
    }));
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div className="flex flex-col items-center justify-center gap-4 p-8 text-center">
          <AlertTriangle className="h-8 w-8 text-accent-gold" />
          <p className="text-sm text-text-secondary">
            Something went wrong rendering this page.
          </p>
          <p className="text-xs text-text-muted max-w-md">
            {this.state.error?.message}
          </p>
          <Button size="small" onClick={this.handleRetry}>
            Retry
          </Button>
        </div>
      );
    }

    return <div key={this.state.retryKey}>{this.props.children}</div>;
  }
}
