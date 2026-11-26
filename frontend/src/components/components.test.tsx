import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { event } from "../test/fixtures";
import { EventsFeed } from "./dashboard/EventsFeed";
import { ShelfShareBars } from "./dashboard/ShelfShareBars";
import { MarketNav } from "./MarketNav";
import { Pager } from "./SectionPage";

describe("EventsFeed", () => {
  it("renders a price event with its values and change", () => {
    render(<EventsFeed events={[event()]} country="US" />);
    expect(screen.getByText("Price drop")).toBeInTheDocument();
    expect(screen.getByText("$100.00")).toBeInTheDocument();
    expect(screen.getByText("$90.00")).toBeInTheDocument();
    expect(screen.getByText("(−10.0%)")).toBeInTheDocument();
  });

  it("shows the caveat only when confidence is not high", () => {
    const { rerender } = render(<EventsFeed events={[event({ confidence: "high" })]} country="US" />);
    expect(screen.queryByText(/Same product at the same retailer/)).not.toBeInTheDocument();
    rerender(<EventsFeed events={[event({ confidence: "low" })]} country="US" />);
    expect(screen.getByText(/Low confidence/)).toBeInTheDocument();
  });

  it("describes removals through the backend summary, not invented numbers", () => {
    render(
      <EventsFeed
        events={[
          event({
            type: "REMOVED_PRODUCT",
            previous_value: 3,
            current_value: null,
            change_pct: null,
            summary: "No longer on the shelf: Sundome.",
          }),
        ]}
        country="US"
      />,
    );
    expect(screen.getByText("Product removed")).toBeInTheDocument();
    expect(screen.getByText("No longer on the shelf: Sundome.")).toBeInTheDocument();
  });

  it("truncates to the teaser size and expands, or shows a whole page when asked", async () => {
    const many = Array.from({ length: 9 }, (_, i) => event({ id: i + 1, product: `Product ${i + 1}` }));
    const { unmount } = render(<EventsFeed events={many} country="US" />);
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
    await userEvent.click(screen.getByRole("button", { name: /show all 9 changes/i }));
    expect(screen.getAllByRole("listitem")).toHaveLength(9);
    unmount();
    render(<EventsFeed events={many} country="US" initialVisible={25} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(9);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("falls back gracefully for event types it does not know", () => {
    render(
      <EventsFeed
        events={[event({ type: "FUTURE_EVENT", previous_value: null, current_value: null, change_pct: null, summary: "Something new." })]}
        country="US"
      />,
    );
    expect(screen.getByText("Future event")).toBeInTheDocument();
    expect(screen.getByText("Something new.")).toBeInTheDocument();
  });
});

describe("ShelfShareBars", () => {
  it("is a bar chart whose table view repeats every brand and marks yours", () => {
    render(
      <ShelfShareBars
        entries={[
          { brand: "Mine", share_pct: 40, is_mine: true, listings: 4 },
          { brand: "Rival", share_pct: 20, is_mine: false, listings: 2 },
        ]}
      />,
    );
    expect(screen.getByRole("img", { name: /Observed visibility share by brand/ })).toBeInTheDocument();
    expect(screen.getByText("Mine (you)")).toBeInTheDocument();
    expect(screen.getByText("40.0%")).toBeInTheDocument();
    expect(screen.getByText("Rival")).toBeInTheDocument();
    expect(screen.getByText(/not sales share/)).toBeInTheDocument();
  });
});

describe("MarketNav", () => {
  it("links every section under the market and highlights the current one", () => {
    render(
      <MemoryRouter initialEntries={["/markets/3/shelf"]}>
        <MarketNav id={3} />
      </MemoryRouter>,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/markets/3",
      "/markets/3/changes",
      "/markets/3/scanner",
      "/markets/3/demand",
      "/markets/3/shelf",
      "/markets/3/opportunities",
      "/markets/3/brief",
      "/markets/3/alerts",
      "/markets/3/replay",
    ]);
    expect(screen.getByRole("link", { name: "Shelf" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveClass("active");
  });
});

describe("Pager", () => {
  it("renders nothing when everything fits on one page", () => {
    const { container } = render(<Pager total={10} limit={25} offset={0} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("pages forward and back and disables the ends", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<Pager total={60} limit={25} offset={0} onChange={onChange} />);
    expect(screen.getByText("1–25 of 60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onChange).toHaveBeenCalledWith(25);
    rerender(<Pager total={60} limit={25} offset={50} onChange={onChange} />);
    expect(screen.getByText("51–60 of 60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onChange).toHaveBeenLastCalledWith(25);
  });
});
