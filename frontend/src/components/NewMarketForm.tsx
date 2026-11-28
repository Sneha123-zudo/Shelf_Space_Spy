import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useCreateMarket, useSuggestMarket } from "../api/hooks";
import { errorMessage } from "../api/errors";
import { ChipEditor, type Chip } from "./ChipEditor";
import { ErrorState } from "./states";
import type { BrandSuggestion } from "../types";

// SerpAPI `gl` codes. The backend lowercases whatever it receives.
const COUNTRIES: { code: string; name: string }[] = [
  { code: "us", name: "United States" },
  { code: "gb", name: "United Kingdom" },
  { code: "in", name: "India" },
  { code: "ca", name: "Canada" },
  { code: "au", name: "Australia" },
  { code: "de", name: "Germany" },
  { code: "fr", name: "France" },
  { code: "es", name: "Spain" },
  { code: "it", name: "Italy" },
  { code: "nl", name: "Netherlands" },
  { code: "jp", name: "Japan" },
  { code: "br", name: "Brazil" },
  { code: "mx", name: "Mexico" },
  { code: "sg", name: "Singapore" },
  { code: "ae", name: "United Arab Emirates" },
  { code: "za", name: "South Africa" },
];

// One Google Trends batch holds at most 5 keywords; values are only comparable inside a batch.
const MAX_KEYWORDS = 5;
const DEFAULT_COMPETITORS = 5;

type Step = "describe" | "review";

const selectedValues = (chips: Chip[]) => chips.filter((chip) => chip.selected).map((chip) => chip.value);

/** Guided setup for a new market: describe it, review the suggested probes and brands, create. Opens the new market's dashboard. */
export function NewMarketForm() {
  const navigate = useNavigate();
  const suggest = useSuggestMarket();
  const create = useCreateMarket();
  const ids = { category: useId(), country: useId(), name: useId(), brand: useId(), brandList: useId() };

  const [step, setStep] = useState<Step>("describe");
  const [category, setCategory] = useState("");
  const [country, setCountry] = useState("us");
  const [name, setName] = useState("");
  const [brandName, setBrandName] = useState("");
  const [keywordChips, setKeywordChips] = useState<Chip[]>([]);
  const [brandChips, setBrandChips] = useState<Chip[]>([]);
  const [brandInfo, setBrandInfo] = useState<Record<string, BrandSuggestion>>({});

  const countryName = COUNTRIES.find((item) => item.code === country)?.name ?? country.toUpperCase();

  function goToReview(keywords: string[], brands: BrandSuggestion[]) {
    setKeywordChips(keywords.map((value, index) => ({ value, selected: index < MAX_KEYWORDS })));
    setBrandChips(brands.map((brand, index) => ({ value: brand.name, selected: index < DEFAULT_COMPETITORS })));
    setBrandInfo(Object.fromEntries(brands.map((brand) => [brand.name.toLowerCase(), brand])));
    // Country is shown next to the name everywhere, so the default name is just the category.
    const trimmed = category.trim();
    if (!name.trim()) setName(trimmed.charAt(0).toUpperCase() + trimmed.slice(1));
    setStep("review");
  }

  function submitDescribe(event: FormEvent) {
    event.preventDefault();
    if (!category.trim()) return;
    suggest.mutate(
      { category: category.trim(), country },
      {
        onSuccess: (data) =>
          goToReview(
            data.keywords,
            [...data.brands].sort((a, b) => b.count - a.count),
          ),
      },
    );
  }

  const keywords = selectedValues(keywordChips);
  const ownBrand = brandName.trim();
  const competitors = selectedValues(brandChips).filter((brand) => brand.toLowerCase() !== ownBrand.toLowerCase());
  const canCreate = name.trim().length > 0 && keywords.length >= 1 && keywords.length <= MAX_KEYWORDS && !create.isPending;

  function submitCreate(event: FormEvent) {
    event.preventDefault();
    if (!canCreate) return;
    create.mutate(
      {
        name: name.trim(),
        country,
        category: category.trim(),
        ...(ownBrand ? { brand_name: ownBrand } : {}),
        keywords,
        competitors,
      },
      { onSuccess: (market) => navigate(`/markets/${market.id}`) },
    );
  }

  const brandChipsWithMeta = brandChips.map((chip) => {
    const info = brandInfo[chip.value.toLowerCase()];
    const isOwn = ownBrand && chip.value.toLowerCase() === ownBrand.toLowerCase();
    return {
      ...chip,
      meta: (
        <>
          {info && <span className="chip-meta">{info.count}×</span>}
          {info?.inferred && (
            <span className="chip-tag" title="Guessed from product titles. Check the spelling.">
              inferred
            </span>
          )}
          {isOwn && <span className="chip-tag">you</span>}
        </>
      ),
    };
  });

  return (
    <>
      <ol className="stepper" aria-label="Setup steps">
        <li aria-current={step === "describe" ? "step" : undefined} className={step === "describe" ? "is-current" : "is-done"}>
          1. Describe the market
        </li>
        <li aria-current={step === "review" ? "step" : undefined} className={step === "review" ? "is-current" : undefined}>
          2. Review and create
        </li>
      </ol>

      {step === "describe" && (
        <form className="panel setup-form" onSubmit={submitDescribe} aria-busy={suggest.isPending}>
          <div className="field-row">
            <label htmlFor={ids.category}>
              Category
              <input
                id={ids.category}
                value={category}
                required
                placeholder="e.g. running shoes, cold brew coffee, dog harness"
                onChange={(event) => setCategory(event.target.value)}
              />
            </label>
            <label htmlFor={ids.country}>
              Country
              <select id={ids.country} value={country} onChange={(event) => setCountry(event.target.value)}>
                {COUNTRIES.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.name} ({item.code.toUpperCase()})
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="field-hint">Suggestions come from Google autocomplete and shopping results for your category.</p>
          {suggest.isError && (
            <div className="setup-error">
              <ErrorState error={suggest.error} title="Couldn't fetch suggestions" />
              <button
                type="button"
                className="button-secondary"
                onClick={() => goToReview(category.trim() ? [category.trim()] : [], [])}
                disabled={!category.trim()}
              >
                Skip and enter probes manually
              </button>
            </div>
          )}
          <div className="form-actions">
            <button type="submit" disabled={!category.trim() || suggest.isPending} aria-busy={suggest.isPending}>
              {suggest.isPending ? "Reading the shelf…" : "Suggest probes and brands"}
            </button>
          </div>
        </form>
      )}

      {step === "review" && (
        <form className="panel setup-form" onSubmit={submitCreate} aria-busy={create.isPending}>
          <p className="setup-summary">
            <strong>{category}</strong> in <strong>{countryName}</strong>
            <button type="button" className="text-button" onClick={() => setStep("describe")}>
              Change
            </button>
          </p>

          <div className="field-row">
            <label htmlFor={ids.name}>
              Market name
              <input id={ids.name} value={name} required onChange={(event) => setName(event.target.value)} />
            </label>
            <label htmlFor={ids.brand}>
              Your brand <span className="optional">(optional)</span>
              <input
                id={ids.brand}
                value={brandName}
                list={ids.brandList}
                placeholder="Leave empty to research only"
                onChange={(event) => setBrandName(event.target.value)}
              />
              <datalist id={ids.brandList}>
                {brandChips.map((chip) => (
                  <option key={chip.value} value={chip.value} />
                ))}
              </datalist>
            </label>
          </div>

          <ChipEditor
            label="Search probes"
            hint={`Up to ${MAX_KEYWORDS} probes so they fit one Google Trends batch (Trends values are only comparable within a batch).`}
            chips={keywordChips}
            onChange={setKeywordChips}
            maxSelected={MAX_KEYWORDS}
            addPlaceholder="Add a probe, e.g. trail running shoes"
          />

          <ChipEditor
            label="Competitor brands"
            hint={
              brandChips.length
                ? "Counts are how often each brand appeared on the sampled shelf. Inferred brands were guessed from product titles, so check them."
                : "No brands suggested. Add the competitors you know, or leave this empty and let scans discover them."
            }
            chips={brandChipsWithMeta}
            onChange={setBrandChips}
            addPlaceholder="Add a competitor brand"
          />

          {keywords.length === 0 && (
            <p className="field-error" role="alert">
              Select at least one search probe.
            </p>
          )}
          {create.isError && (
            <p className="notice error notice-error" role="alert">
              {errorMessage(create.error)}
            </p>
          )}

          <div className="form-actions">
            <button type="button" className="button-secondary" onClick={() => setStep("describe")}>
              Back
            </button>
            <button type="submit" disabled={!canCreate} aria-busy={create.isPending}>
              {create.isPending ? "Creating market…" : `Create market with ${keywords.length} probe${keywords.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </form>
      )}
    </>
  );
}
