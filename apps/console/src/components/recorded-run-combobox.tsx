"use client";

import { useEffect, useId, useMemo, useRef, useState, useTransition } from "react";
import type { FocusEvent, KeyboardEvent, MouseEvent } from "react";
import { useRouter } from "next/navigation";

type RecordedRunComboboxProps = {
  options: string[];
  selectedSku: string;
};

export function RecordedRunCombobox({ options, selectedSku }: RecordedRunComboboxProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const generatedId = useId();
  const inputId = `${generatedId}-input`;
  const listboxId = `${generatedId}-listbox`;
  const statusId = `${generatedId}-status`;

  const [inputValue, setInputValue] = useState(selectedSku);
  const [isEdited, setIsEdited] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() => Math.max(options.indexOf(selectedSku), 0));
  const [isPending, startTransition] = useTransition();

  const filteredOptions = useMemo(() => {
    if (!isEdited) return options;

    const query = inputValue.trim().toLocaleLowerCase();
    if (!query) return options;
    return options.filter((option) => option.toLocaleLowerCase().includes(query));
  }, [inputValue, isEdited, options]);

  useEffect(() => {
    setInputValue(selectedSku);
    setIsEdited(false);
    setIsOpen(false);
    setActiveIndex(Math.max(options.indexOf(selectedSku), 0));
  }, [options, selectedSku]);

  useEffect(() => {
    if (!isOpen || activeIndex < 0) return;
    document
      .getElementById(`${listboxId}-option-${activeIndex}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, filteredOptions, isOpen, listboxId]);

  function openOptions() {
    setInputValue(selectedSku);
    setIsEdited(false);
    setIsOpen(true);
    setActiveIndex(Math.max(options.indexOf(selectedSku), 0));
  }

  function closeOptions() {
    setInputValue(selectedSku);
    setIsEdited(false);
    setIsOpen(false);
  }

  function moveActiveOption(step: 1 | -1) {
    if (filteredOptions.length === 0) return;
    setActiveIndex((current) => {
      if (current < 0 || current >= filteredOptions.length) {
        return step === 1 ? 0 : filteredOptions.length - 1;
      }
      return (current + step + filteredOptions.length) % filteredOptions.length;
    });
  }

  function selectRun(sku: string) {
    setInputValue(sku);
    setIsEdited(false);
    setIsOpen(false);

    if (sku === selectedSku) return;
    startTransition(() => {
      router.push(`/pipeline?sku=${encodeURIComponent(sku)}`);
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (isOpen) moveActiveOption(1);
        else openOptions();
        break;
      case "ArrowUp":
        event.preventDefault();
        if (isOpen) moveActiveOption(-1);
        else openOptions();
        break;
      case "Home":
        if (!isOpen || filteredOptions.length === 0) return;
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        if (!isOpen || filteredOptions.length === 0) return;
        event.preventDefault();
        setActiveIndex(filteredOptions.length - 1);
        break;
      case "Enter": {
        if (!isOpen) return;
        event.preventDefault();
        const activeOption = filteredOptions[activeIndex];
        if (activeOption) selectRun(activeOption);
        break;
      }
      case "Escape":
        if (!isOpen) return;
        event.preventDefault();
        closeOptions();
        break;
    }
  }

  function handleBlur(event: FocusEvent<HTMLDivElement>) {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    closeOptions();
  }

  function keepInputFocused(event: MouseEvent) {
    event.preventDefault();
  }

  const activeOption = filteredOptions[activeIndex];
  const resultLabel = isPending
    ? `Loading ${inputValue}`
    : isOpen
      ? filteredOptions.length === 0
        ? `No recorded runs match “${inputValue}”.`
        : `${filteredOptions.length} recorded ${filteredOptions.length === 1 ? "run" : "runs"} found.`
      : `Selected run: ${selectedSku}`;

  return (
    <div className="recorded-run-combobox" onBlur={handleBlur}>
      <label className="overline" htmlFor={inputId}>
        Recorded runs
      </label>

      <div className="recorded-run-control">
        <svg className="recorded-run-search-icon" viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="8.5" cy="8.5" r="5.5" />
          <path d="m12.5 12.5 4 4" />
        </svg>
        <input
          ref={inputRef}
          id={inputId}
          type="search"
          role="combobox"
          className="recorded-run-input mono"
          value={inputValue}
          autoComplete="off"
          spellCheck={false}
          aria-autocomplete="list"
          aria-busy={isPending || undefined}
          aria-controls={listboxId}
          aria-describedby={statusId}
          aria-expanded={isOpen}
          aria-activedescendant={
            isOpen && activeOption ? `${listboxId}-option-${activeIndex}` : undefined
          }
          onFocus={(event) => {
            event.currentTarget.select();
            if (!isOpen) openOptions();
          }}
          onClick={() => {
            if (!isOpen) openOptions();
          }}
          onChange={(event) => {
            setInputValue(event.target.value);
            setIsEdited(true);
            setIsOpen(true);
            setActiveIndex(0);
          }}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className="recorded-run-toggle icon-target"
          aria-label={isOpen ? "Close recorded runs" : "Open recorded runs"}
          aria-controls={listboxId}
          aria-expanded={isOpen}
          onMouseDown={keepInputFocused}
          onClick={() => {
            if (isOpen) closeOptions();
            else {
              inputRef.current?.focus();
              openOptions();
            }
          }}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="m5.5 7.5 4.5 4.5 4.5-4.5" />
          </svg>
        </button>
      </div>

      <p id={statusId} className="recorded-run-status" role="status" aria-live="polite">
        {resultLabel}
      </p>

      {isOpen ? (
        <div className="recorded-run-menu">
          <ul id={listboxId} role="listbox" aria-label="Recorded runs">
            {filteredOptions.length > 0 ? (
              filteredOptions.map((sku, index) => {
                const isSelected = sku === selectedSku;
                const isActive = index === activeIndex;

                return (
                  <li
                    id={`${listboxId}-option-${index}`}
                    key={sku}
                    role="option"
                    className="recorded-run-option"
                    aria-selected={isSelected}
                    data-active={isActive}
                    data-selected={isSelected}
                    onMouseDown={keepInputFocused}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => selectRun(sku)}
                  >
                    <span className="mono">{sku}</span>
                    {isSelected ? (
                      <svg viewBox="0 0 20 20" aria-hidden="true">
                        <path d="m4.5 10.5 3.25 3.25 7.75-8" />
                      </svg>
                    ) : null}
                  </li>
                );
              })
            ) : (
              <li role="presentation" className="recorded-run-empty">
                No run matches <span className="mono">{inputValue}</span>
              </li>
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
