import { useId, useState, type KeyboardEvent, type ReactNode } from "react";

const normalize = (value: string) => value.trim().replace(/\s+/g, " ");

export type Chip = { value: string; selected: boolean; meta?: ReactNode };

/**
 * Editable chip list: click a chip to include/exclude it, remove it with ×,
 * or type a new one and press Enter. Duplicates (case-insensitive) are ignored.
 */
export function ChipEditor({
  label,
  hint,
  chips,
  onChange,
  maxSelected,
  addPlaceholder,
}: {
  label: string;
  hint?: ReactNode;
  chips: Chip[];
  onChange: (chips: Chip[]) => void;
  maxSelected?: number;
  addPlaceholder: string;
}) {
  const [draft, setDraft] = useState("");
  const inputId = useId();
  const hintId = useId();
  const selectedCount = chips.filter((chip) => chip.selected).length;
  const atLimit = maxSelected !== undefined && selectedCount >= maxSelected;

  function add() {
    const value = normalize(draft);
    if (!value) return;
    const existing = chips.find((chip) => chip.value.toLowerCase() === value.toLowerCase());
    if (existing) {
      if (!existing.selected && !atLimit) onChange(chips.map((chip) => (chip === existing ? { ...chip, selected: true } : chip)));
    } else {
      onChange([...chips, { value, selected: !atLimit }]);
    }
    setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  }

  return (
    <fieldset className="chip-editor">
      <legend>
        {label}
        {maxSelected !== undefined && (
          <span className="chip-count">
            {" "}
            {selectedCount}/{maxSelected} selected
          </span>
        )}
      </legend>
      {hint && (
        <p className="field-hint" id={hintId}>
          {hint}
        </p>
      )}
      <ul className="chip-list">
        {chips.map((chip) => {
          const blocked = !chip.selected && atLimit;
          return (
            <li key={chip.value} className={chip.selected ? "chip is-selected" : "chip"}>
              <button
                type="button"
                className="chip-toggle"
                aria-pressed={chip.selected}
                disabled={blocked}
                title={blocked ? `Limit of ${maxSelected} reached. Deselect one first.` : undefined}
                onClick={() => onChange(chips.map((item) => (item === chip ? { ...item, selected: !item.selected } : item)))}
              >
                <span className="chip-check" aria-hidden="true">
                  {chip.selected ? "✓" : "+"}
                </span>
                <span>{chip.value}</span>
                {chip.meta}
              </button>
              <button
                type="button"
                className="chip-remove"
                aria-label={`Remove ${chip.value}`}
                onClick={() => onChange(chips.filter((item) => item !== chip))}
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
      <div className="chip-add">
        <label className="visually-hidden" htmlFor={inputId}>
          {addPlaceholder}
        </label>
        <input
          id={inputId}
          value={draft}
          placeholder={addPlaceholder}
          aria-describedby={hint ? hintId : undefined}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="button-secondary" onClick={add} disabled={!normalize(draft)}>
          Add
        </button>
      </div>
    </fieldset>
  );
}
