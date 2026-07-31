import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/features/admin/StatusBadge";

describe("StatusBadge", () => {
  it("renders a label per integration status", () => {
    const { rerender } = render(<StatusBadge status="OK" />);
    expect(screen.getByText("OK")).toBeInTheDocument();

    rerender(<StatusBadge status="FAILED" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();

    rerender(<StatusBadge status="UNVERIFIED" />);
    expect(screen.getByText("Unverified")).toBeInTheDocument();
  });
});
