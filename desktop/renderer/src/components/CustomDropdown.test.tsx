import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CustomDropdown, type DropdownOption } from "./CustomDropdown";

const TEST_OPTIONS: readonly DropdownOption<"opt-a" | "opt-b">[] = [
  { value: "opt-a", label: "Option Alpha", hint: "First alpha option" },
  { value: "opt-b", label: "Option Beta", hint: "Second beta option" },
];

describe("CustomDropdown", () => {
  it("renders trigger with combobox role, accessibility attributes, and selected label", () => {
    const markup = renderToStaticMarkup(
      <CustomDropdown
        id="test-dropdown"
        labelId="test-label"
        value="opt-a"
        options={TEST_OPTIONS}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain('id="test-dropdown"');
    expect(markup).toContain('role="combobox"');
    expect(markup).toContain('aria-haspopup="listbox"');
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('aria-controls="test-dropdown-listbox"');
    expect(markup).toContain('aria-labelledby="test-label test-dropdown"');
    expect(markup).toContain("Option Alpha");
    expect(markup).toContain("custom-dropdown-chevron");
  });

  it("reflects disabled state on the trigger button", () => {
    const markup = renderToStaticMarkup(
      <CustomDropdown
        id="disabled-dropdown"
        value="opt-b"
        options={TEST_OPTIONS}
        disabled={true}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain("disabled=\"\"");
    expect(markup).toContain("Option Beta");
  });

  it("includes hidden native select with all options for form and fallback compatibility", () => {
    const markup = renderToStaticMarkup(
      <CustomDropdown
        id="form-dropdown"
        name="selection_field"
        value="opt-b"
        options={TEST_OPTIONS}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain('name="selection_field"');
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain('class="sr-only"');
    expect(markup).toContain('value="opt-a"');
    expect(markup).toContain('value="opt-b"');
    expect(markup).toContain('selected=""');
  });
});
