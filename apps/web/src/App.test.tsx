import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "@/App";

describe("App", () => {
  it("renders the product name and sprint list", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "Taproot" })).toBeInTheDocument();
    expect(screen.getByText(/0 · Foundations/)).toBeInTheDocument();
  });
});
