/**
 * Read a control's on/off state from ARIA, not from a component library.
 *
 * The harness used to read `data-state="checked"`, an attribute Radix
 * primitives set and other implementations do not. On the compliance tab's
 * "Has obligations" switch the test id sits on the `<input>` while
 * `data-state` sits on the wrapping `<label>`, so the read returned `null`
 * every time and the "is it already on?" branch never ran. Nothing failed,
 * because the switch starts off.
 *
 * `aria-checked` (checkbox and switch roles) and `aria-pressed` (toggle
 * buttons) are what assistive technology reads, so every switch has to set
 * one of them. A control that sets neither is a defect in the control, and
 * the read throws instead of answering "off".
 *
 * Takes the minimal shape of a Playwright `Locator` so the unit test can
 * exercise it without a browser.
 */
export interface AttributeReader {
  getAttribute(name: string): Promise<string | null>;
}

export async function readToggleOn(control: AttributeReader): Promise<boolean> {
  for (const name of ["aria-checked", "aria-pressed"]) {
    const value = await control.getAttribute(name);
    if (value === "true") return true;
    if (value === "false") return false;
  }
  throw new Error(
    "toggle exposes neither aria-checked nor aria-pressed as true/false",
  );
}
