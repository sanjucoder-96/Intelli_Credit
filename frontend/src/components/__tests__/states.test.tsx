import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState, ErrorState } from "@/components/shared/states";
import { ScoreGauge } from "@/components/shared/viz";
import { ApiError } from "@/lib/api";

describe("states + viz", () => {
  it("renders an empty state with title + description", () => {
    render(<EmptyState title="No decisions yet" description="Run an appraisal." />);
    expect(screen.getByText("No decisions yet")).toBeInTheDocument();
    expect(screen.getByText("Run an appraisal.")).toBeInTheDocument();
  });

  it("renders a friendly message when the API is unreachable", () => {
    render(<ErrorState error={new ApiError(0, "network_error", "down")} />);
    expect(screen.getByText(/API is unreachable/i)).toBeInTheDocument();
  });

  it("renders the score in the gauge, or an em dash when null", () => {
    const { rerender } = render(<ScoreGauge value={84.5} />);
    expect(screen.getByText("84.5")).toBeInTheDocument();
    rerender(<ScoreGauge value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
