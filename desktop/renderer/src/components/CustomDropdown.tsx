import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

export interface DropdownOption<T extends string = string> {
  value: T;
  label: string;
  hint?: string;
}

export interface CustomDropdownProps<T extends string = string> {
  id: string;
  labelId?: string;
  name?: string;
  value: T;
  options: readonly DropdownOption<T>[];
  disabled?: boolean;
  className?: string;
  onChange: (value: T) => void;
}

export function CustomDropdown<T extends string = string>({
  id,
  labelId,
  name,
  value,
  options,
  disabled = false,
  className = "",
  onChange,
}: CustomDropdownProps<T>) {
  const [isOpen, setIsOpen] = useState(false);
  const [isOpening, setIsOpening] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const selectedIndex = options.findIndex((opt) => opt.value === value);
  const selectedOption = options[selectedIndex] ?? options[0];
  const listboxId = `${id}-listbox`;

  const openDropdown = useCallback(() => {
    if (disabled) return;
    setIsClosing(false);
    setIsOpening(true);
    setIsOpen(true);
    setFocusedIndex(selectedIndex >= 0 ? selectedIndex : 0);
  }, [disabled, selectedIndex]);

  const closeDropdown = useCallback(() => {
    if (!isOpen || isClosing) return;
    setIsOpening(false);
    setIsClosing(true);
  }, [isOpen, isClosing]);

  const handleAnimationEnd = useCallback(
    (event: React.AnimationEvent<HTMLUListElement>) => {
      // Only react to animations directly on the menu, not child elements (like checkmark pop)
      if (event.target !== event.currentTarget) return;

      if (isClosing) {
        setIsClosing(false);
        setIsOpen(false);
      } else if (isOpening) {
        setIsOpening(false);
      }
    },
    [isClosing, isOpening],
  );

  // Fallback timeout ensures opening animation settles cleanly even if animation events are skipped
  useEffect(() => {
    if (isOpening) {
      const timer = setTimeout(() => {
        setIsOpening(false);
      }, 220);
      return () => clearTimeout(timer);
    }
  }, [isOpening]);

  // Fallback timeout ensures state completes even if animation events are skipped
  useEffect(() => {
    if (isClosing) {
      const timer = setTimeout(() => {
        setIsClosing(false);
        setIsOpen(false);
      }, 220);
      return () => clearTimeout(timer);
    }
  }, [isClosing]);

  // Close immediately when disabled changes to true
  useEffect(() => {
    if (disabled && isOpen) {
      setIsOpening(false);
      setIsClosing(false);
      setIsOpen(false);
    }
  }, [disabled, isOpen]);

  // Click outside listener
  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        closeDropdown();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [isOpen, closeDropdown]);

  const handleSelect = (optionValue: T) => {
    onChange(optionValue);
    closeDropdown();
    triggerRef.current?.focus();
  };

  const handleTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!isOpen) {
          openDropdown();
        } else {
          setFocusedIndex((prev) => (prev + 1) % options.length);
        }
        break;

      case "ArrowUp":
        event.preventDefault();
        if (!isOpen) {
          openDropdown();
        } else {
          setFocusedIndex((prev) => (prev - 1 + options.length) % options.length);
        }
        break;

      case "Home":
        if (isOpen) {
          event.preventDefault();
          setFocusedIndex(0);
        }
        break;

      case "End":
        if (isOpen) {
          event.preventDefault();
          setFocusedIndex(options.length - 1);
        }
        break;

      case "Enter":
      case " ":
        event.preventDefault();
        if (!isOpen) {
          openDropdown();
        } else if (focusedIndex >= 0 && focusedIndex < options.length) {
          handleSelect(options[focusedIndex].value);
        }
        break;

      case "Escape":
        if (isOpen) {
          event.preventDefault();
          closeDropdown();
          triggerRef.current?.focus();
        }
        break;

      case "Tab":
        if (isOpen) {
          closeDropdown();
        }
        break;

      default:
        break;
    }
  };

  return (
    <div
      ref={containerRef}
      className={`custom-dropdown ${className}`.trim()}
    >
      {/* Visually hidden native select to guarantee static render, form, and fallback compatibility */}
      <select
        id={`${id}-native`}
        name={name ?? id}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        tabIndex={-1}
        aria-hidden="true"
        className="sr-only"
        disabled={disabled}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      <button
        ref={triggerRef}
        type="button"
        id={id}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={listboxId}
        aria-labelledby={labelId ? `${labelId} ${id}` : undefined}
        aria-activedescendant={
          isOpen && focusedIndex >= 0
            ? `${id}-opt-${options[focusedIndex].value}`
            : undefined
        }
        className={`custom-dropdown-trigger ${isOpen ? "is-open" : ""}`}
        disabled={disabled}
        onClick={() => {
          if (isOpen) {
            closeDropdown();
          } else {
            openDropdown();
          }
        }}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className="custom-dropdown-trigger-text">
          {selectedOption?.label}
        </span>
        <span className="custom-dropdown-chevron" aria-hidden="true">
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
          >
            <path
              d="M2.5 4.5L6 8L9.5 4.5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>

      {(isOpen || isClosing) && (
        <ul
          id={listboxId}
          role="listbox"
          tabIndex={-1}
          aria-labelledby={labelId}
          className={`custom-dropdown-menu ${isOpening ? "is-opening" : ""} ${
            isClosing ? "is-closing" : ""
          }`.trim()}
          onAnimationEnd={handleAnimationEnd}
        >
          {options.map((opt, index) => {
            const isSelected = opt.value === value;
            const isFocused = index === focusedIndex;

            return (
              <li
                key={opt.value}
                id={`${id}-opt-${opt.value}`}
                role="option"
                aria-selected={isSelected}
                className={`custom-dropdown-option ${
                  isSelected ? "is-selected" : ""
                } ${isFocused ? "is-focused" : ""}`}
                onClick={() => handleSelect(opt.value)}
                onMouseEnter={() => setFocusedIndex(index)}
              >
                <div className="custom-dropdown-option-main">
                  <span className="custom-dropdown-option-title">
                    {opt.label}
                  </span>
                  {opt.hint && (
                    <span className="custom-dropdown-option-hint">
                      {opt.hint}
                    </span>
                  )}
                </div>
                {isSelected && (
                  <span className="custom-dropdown-option-check" aria-hidden="true">
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 12 12"
                      fill="none"
                    >
                      <path
                        d="M2.5 6.2L4.8 8.5L9.5 3.8"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
