import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  DecisionBadge,
  GradeBadge,
  SeverityBadge,
  StatusBadge,
} from "@/components/shared/risk";

describe("risk badges", () => {
  it("renders a grade, or 'Ungraded' when absent", () => {
    const { rerender } = render(<GradeBadge grade="IC2" />);
    expect(screen.getByText("IC2")).toBeInTheDocument();
    rerender(<GradeBadge grade={null} />);
    expect(screen.getByText("Ungraded")).toBeInTheDocument();
  });

  it("renders a decision, or 'Pending' when absent", () => {
    const { rerender } = render(<DecisionBadge decision="approve" />);
    expect(screen.getByText("approve")).toBeInTheDocument();
    rerender(<DecisionBadge decision={null} />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("renders severity + humanised status", () => {
    render(<SeverityBadge severity="high" />);
    expect(screen.getByText("high")).toBeInTheDocument();
    render(<StatusBadge status="paused_hitl" />);
    expect(screen.getByText("Paused Hitl")).toBeInTheDocument();
  });
});
