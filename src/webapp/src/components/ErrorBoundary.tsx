import React from "react";

type ErrorBoundaryVariant = "top-level" | "section";

interface ErrorBoundaryProps {
  children: React.ReactNode;
  // Human-readable label for the wrapped region (e.g. "Telemetry") shown in
  // the section-variant fallback so the user knows which panel crashed.
  sectionLabel?: string;
  variant?: ErrorBoundaryVariant;
}

interface ErrorBoundaryState {
  error: Error | null;
}

const INITIAL_STATE: ErrorBoundaryState = { error: null };

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = INITIAL_STATE;

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // Surface the crash + component stack so it's easy to triage from devtools
    // even when the fallback is rendered.
    console.error("[ErrorBoundary]", {
      sectionLabel: this.props.sectionLabel ?? null,
      variant: this.props.variant ?? "section",
      message: error.message,
      stack: error.stack,
      componentStack: info.componentStack,
    });
  }

  private handleReset = (): void => {
    this.setState(INITIAL_STATE);
  };

  private handleReload = (): void => {
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  };

  render(): React.ReactNode {
    if (this.state.error === null) {
      return this.props.children;
    }

    const variant = this.props.variant ?? "section";
    if (variant === "top-level") {
      return (
        <div role="alert" className="error-boundary error-boundary--top-level">
          <h1>Webapp crashed</h1>
          <p>
            An uncaught error stopped the UI from rendering. Reload to recover; if it keeps happening,
            check the browser console for the stack trace.
          </p>
          <pre className="error-boundary__detail">{this.state.error.message}</pre>
          <button type="button" className="error-boundary__action" onClick={this.handleReload}>
            Reload page
          </button>
        </div>
      );
    }

    const label = this.props.sectionLabel ?? "Section";
    return (
      <section role="alert" className="panel error-boundary error-boundary--section">
        <h2>{`${label} unavailable`}</h2>
        <p>This panel hit a render error and was isolated so the rest of the UI keeps working.</p>
        <pre className="error-boundary__detail">{this.state.error.message}</pre>
        <button type="button" className="error-boundary__action" onClick={this.handleReset}>
          Try again
        </button>
      </section>
    );
  }
}

export default ErrorBoundary;
