/** UI-only bridge from the account menu to the license dialog. */
export const LICENSE_OPEN_EVENT = "maitu:license-open";

export function requestLicenseDialog(): void {
  window.dispatchEvent(new Event(LICENSE_OPEN_EVENT));
}
