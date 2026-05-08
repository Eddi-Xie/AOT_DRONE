import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): JSX.Element {
  throw new Error("kaboom from child");
}

describe("ErrorBoundary", () => {
  it("renders children when no error is thrown", () => {
    const html = renderToString(
      <ErrorBoundary sectionLabel="Test">
        <p data-testid="ok">healthy child</p>
      </ErrorBoundary>,
    );
    expect(html).toContain("healthy child");
    expect(html).not.toContain("unavailable");
  });

  it("renders the section fallback markup when getDerivedStateFromError fires", () => {
    // renderToString swallows render-thrown errors but invokes the boundary's
    // static method. We inspect the boundary's output via the static helper +
    // a manual render of the fallback path to verify structure.
    const stateAfterError = ErrorBoundary.getDerivedStateFromError(new Error("kaboom"));
    expect(stateAfterError.error).toBeInstanceOf(Error);
    expect(stateAfterError.error?.message).toBe("kaboom");

    // Render the section fallback directly by priming state via a subclass so
    // we don't rely on SSR error semantics.
    class PrimedBoundary extends ErrorBoundary {
      state = { error: new Error("kaboom from child") };
    }
    const html = renderToString(
      <PrimedBoundary sectionLabel="Telemetry">
        <Boom />
      </PrimedBoundary>,
    );
    expect(html).toContain("Telemetry unavailable");
    expect(html).toContain("kaboom from child");
    expect(html).toContain("Try again");
    expect(html).toContain('role="alert"');
  });

  it("renders the top-level fallback when variant=top-level", () => {
    class PrimedBoundary extends ErrorBoundary {
      state = { error: new Error("top-crash") };
    }
    const html = renderToString(
      <PrimedBoundary variant="top-level">
        <Boom />
      </PrimedBoundary>,
    );
    expect(html).toContain("Webapp crashed");
    expect(html).toContain("top-crash");
    expect(html).toContain("Reload page");
  });

  it("getDerivedStateFromError returns the error in state", () => {
    const err = new Error("derive-me");
    const state = ErrorBoundary.getDerivedStateFromError(err);
    expect(state).toEqual({ error: err });
  });

  it("componentDidCatch logs the section label + stack to console.error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const boundary = new ErrorBoundary({ sectionLabel: "Foo", children: null });
      boundary.componentDidCatch(new Error("info-test"), { componentStack: "fake-stack" });
      expect(spy).toHaveBeenCalledWith(
        "[ErrorBoundary]",
        expect.objectContaining({
          sectionLabel: "Foo",
          message: "info-test",
          componentStack: "fake-stack",
        }),
      );
    } finally {
      spy.mockRestore();
    }
  });

  it("private handleReset clears state.error so the wrapped subtree can re-mount", () => {
    // We don't have RTL or jsdom; simulate the reset path by calling the
    // boundary instance method directly. Pre-load state.error, invoke
    // setState's effective behaviour by reaching into the instance.
    const boundary = new ErrorBoundary({ sectionLabel: "Foo", children: null });
    boundary.state = { error: new Error("primed") };
    // setState is normally sync-batched by React, but we own the instance
    // here so emulate it. The handler is private; access via `as any` for
    // the test only.
    const setStateCalls: Array<{ error: Error | null }> = [];
    boundary.setState = ((updater: { error: Error | null }) => {
      setStateCalls.push(updater);
    }) as unknown as typeof boundary.setState;

    // Drive the private handler.
    (boundary as unknown as { handleReset: () => void }).handleReset();

    expect(setStateCalls).toHaveLength(1);
    expect(setStateCalls[0]).toEqual({ error: null });
  });
});
