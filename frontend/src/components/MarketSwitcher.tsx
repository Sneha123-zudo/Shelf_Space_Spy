import { useId } from "react";
import { useMatch, useNavigate } from "react-router-dom";

import { useMarkets } from "../api/hooks";
import { countryLabel } from "../lib/format";

export function MarketSwitcher() {
  const markets = useMarkets();
  const navigate = useNavigate();
  const match = useMatch("/markets/:id/*");
  const id = useId();
  const currentId = match?.params.id && /^\d+$/.test(match.params.id) ? match.params.id : "";
  // Keep the open section (changes, shelf, ...) when switching markets.
  const section = match?.params["*"] ? `/${match.params["*"]}` : "";

  let placeholder = "Switch market…";
  if (markets.isPending) placeholder = "Loading markets…";
  else if (markets.isError && !markets.data) placeholder = "Markets unavailable";
  else if (markets.data?.length === 0) placeholder = "No markets yet";

  return (
    <div className="market-switcher">
      <label htmlFor={id} className="visually-hidden">
        Current market
      </label>
      <select
        id={id}
        value={currentId}
        disabled={!markets.data?.length}
        onChange={(event) => {
          if (event.target.value) navigate(`/markets/${event.target.value}${section}`);
        }}
      >
        <option value="" disabled>
          {placeholder}
        </option>
        {markets.data?.map((market) => (
          <option key={market.id} value={String(market.id)}>
            {market.name} · {countryLabel(market.country)}
          </option>
        ))}
      </select>
    </div>
  );
}
