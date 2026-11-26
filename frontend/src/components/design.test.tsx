import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { event } from "../test/fixtures";
import type { Opportunity } from "../types";
import { OpportunityCards } from "./dashboard/OpportunityCards";
import { EventTimeline } from "./EventTimeline";
import { ShelfHeatmap } from "./ShelfHeatmap";
import { Badge, ConfidenceBadge, InferredBadge, SeverityBadge, UnstableBadge } from "./ui/Badge";
import { Drawer } from "./ui/Drawer";
import { InfoTip } from "./ui/InfoTip";
import { buildHeatmap } from "../lib/shelf";

describe("badges", () => {
  it("say what they mean in text, not color alone", () => {
    render(
      <>
        <SeverityBadge level="high" />
        <ConfidenceBadge level="low" />
        <ConfidenceBadge level={undefined} />
        <InferredBadge />
        <UnstableBadge overlap={0.12} />
        <Badge>Plain</Badge>
      </>,
    );
    for (const text of [
      "High severity",
      "Low confidence",
      "Confidence unknown",
      "Inferred brand",
      "Unstable shelf (12% overlap)",
      "Plain",
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
  });
});

describe("InfoTip", () => {
  it("exposes its explanation to assistive technology", () => {
    render(<InfoTip label="scores">Scores are 0-100.</InfoTip>);
    const button = screen.getByRole("button", { name: "About scores" });
    expect(button).toHaveAccessibleDescription("Scores are 0-100.");
  });
});

describe("Drawer", () => {
  function Harness({ onClose = () => {} }: { onClose?: () => void }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Open it</button>
        <Drawer
          open={open}
          title="Details"
          onClose={() => {
            setOpen(false);
            onClose();
          }}
        >
          <p>Body</p>
        </Drawer>
      </>
    );
  }

  it("opens as a labelled modal dialog, closes on Escape and returns focus to the opener", async () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "Open it" });
    await userEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Details" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("closes from the Close button and the scrim", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Open it" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open it" }));
    await userEvent.click(screen.getByLabelText("Close Details"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("EventTimeline", () => {
  it("shows severity and confidence separately and reports the selected event", async () => {
    const onSelect = vi.fn();
    const chosen = event({ id: 4, severity: "high", confidence: "low", summary: "Rank jumped." });
    render(<EventTimeline events={[chosen]} country="US" onSelect={onSelect} />);
    expect(screen.getByText("High severity")).toBeInTheDocument();
    expect(screen.getByText("Low confidence")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Rank jumped/ }));
    expect(onSelect).toHaveBeenCalledWith(chosen);
  });
});

describe("OpportunityCards", () => {
  const item: Opportunity = {
    keyword: "widget",
    score: 71.3,
    label: "Attractive",
    demand_growth: 18,
    supply_growth: -4,
    reasons: ["Search interest up 18%."],
    caveats: ["Thin data."],
    components: { demand: 80, room: 60, incumbent_pressure: 55, price_headroom: 40 },
    confidence: { level: "low", factors: { data_volume: "thin", scan_count: 1, shelf_stability: null } },
  };

  it("keeps score and confidence separate, and opens an evidence drawer with the components", async () => {
    render(<OpportunityCards opportunities={[item]} />);
    expect(screen.getByRole("meter", { name: /Opportunity score for widget/ })).toHaveAttribute("aria-valuenow", "71");
    expect(screen.getByText("Low confidence")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Evidence/ }));
    const drawer = screen.getByRole("dialog", { name: "widget" });
    expect(drawer).toHaveTextContent(/Confidence \(separate from the score\)/);
    expect(drawer).toHaveTextContent(/It never changes the score/);
    expect(drawer).toHaveTextContent(/Not enough history yet/);
    expect(drawer).toHaveTextContent(/Stability could not be measured yet/);
    expect(drawer).toHaveTextContent(/Shelf room/);
    expect(drawer).toHaveTextContent(/Thin data/);
    expect(screen.getByRole("meter", { name: "Shelf room" })).toHaveAttribute("aria-valuenow", "60");
  });
});

describe("ShelfHeatmap", () => {
  it("prints every value, dashes absent cells with an explanation and flags unstable probes", () => {
    const model = buildHeatmap({
      "probe a": [{ brand: "Mine", share_pct: 12.5, listings: 2, is_mine: true }],
      "probe b": [{ brand: "Rival", share_pct: 30, listings: 3, is_mine: false }],
    });
    render(<ShelfHeatmap model={model} stability={{ "probe a": 0.1, "probe b": 0.9 }} />);
    expect(screen.getByRole("table", { name: /visibility share by brand and probe/i })).toBeInTheDocument();
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.getAllByText(/Unstable shelf/)).toHaveLength(1);
    expect(screen.getByText(/not that it has 0%/)).toBeInTheDocument();
  });
});

describe("RecommendedActions", () => {
  const item = {
    id: "investigate:widget:0",
    kind: "investigate",
    priority: "high" as const,
    probe: "widget",
    title: "Investigate “widget”",
    summary: "Golden: search interest is up.",
    evidence: [
      { label: "Opportunity score", value: 82, kind: "score" as const },
      { label: "Search interest change", value: 18, kind: "pct_signed" as const },
      { label: "Median", value: 30, kind: "price" as const },
    ],
    action: "Research this segment.",
    confidence: "low",
    limitations: ["Only 2 scans of history."],
    uses_demo_data: true,
    source: "opportunity",
  };

  async function renderWith(data: unknown, error: unknown = null) {
    const hooks = await import("../api/hooks");
    vi.spyOn(hooks, "useRecommendations").mockReturnValue({ data, error, isFetching: false, refetch: vi.fn() } as never);
    const { RecommendedActions } = await import("./RecommendedActions");
    return render(<RecommendedActions marketId={1} country="US" />);
  }

  it("shows the action with its evidence, confidence and limitations, says it is a hypothesis, and never mentions demo data", async () => {
    await renderWith({ has_data: true, scan: null, items: [item], notes: [] });
    expect(screen.getByText("Investigate")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Investigate “widget”/ })).toBeInTheDocument();
    expect(screen.getByText("82/100")).toBeInTheDocument();
    expect(screen.getByText("+18.0%")).toBeInTheDocument();
    expect(screen.getByText("$30.00")).toBeInTheDocument();
    expect(screen.getByText("Low confidence")).toBeInTheDocument();
    expect(screen.queryByText(/demo/i)).not.toBeInTheDocument();
    expect(screen.getByText("Research this segment.")).toBeInTheDocument();
    expect(screen.getByText(/Limitations \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Hypotheses, not guaranteed business advice/)).toBeInTheDocument();
  });

  it("shows an empty state and an error state instead of nothing", async () => {
    const { unmount } = await renderWith({ has_data: false, scan: null, items: [], notes: [] });
    expect(screen.getByText("No recommendations yet")).toBeInTheDocument();
    unmount();
    await renderWith(undefined, new Error("boom"));
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn't load recommended actions/i);
  });
});

describe("stored demo wording", () => {
  it("is hidden in the opportunity evidence drawer and its caveat count", async () => {
    const stored = {
      keyword: "widget",
      score: 60,
      label: "Attractive",
      demand_growth: 5,
      supply_growth: 1,
      reasons: ["Search interest up 5%."],
      caveats: ["Includes demo historical data.", "Only 1 scan of history."],
      components: {},
      confidence: { level: "medium", factors: {} },
    } as unknown as Opportunity;
    render(<OpportunityCards opportunities={[stored]} />);
    expect(screen.getByRole("button", { name: /Evidence · 1 caveat →/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Evidence/ }));
    const drawer = screen.getByRole("dialog", { name: "widget" });
    expect(drawer).toHaveTextContent(/Only 1 scan of history/);
    expect(drawer.textContent?.toLowerCase()).not.toContain("demo");
  });
});
