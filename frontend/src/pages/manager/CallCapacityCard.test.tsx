import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { VoiceCapacity } from "../../api/voiceCapacity";
import en from "../../i18n/en";
import CallCapacityCard, { CapacityRequestFields } from "./CallCapacityCard";

const cap: VoiceCapacity = {
  voice_locations: [{ location_id: "loc1", name: "Downtown" }],
  allocation: 10,
  busiest: null,
  pending_request: null,
  default_staff_transfer_number: null,
  max_expected_peak: 500,
};

const c = en.reservations.billing.capacity;
const noop = () => {};

const fields = (expectedPeak: string, locationIds = ["loc1"]) =>
  renderToStaticMarkup(
    <CapacityRequestFields
      cap={cap}
      form={{ locationIds, expectedPeak, neededBy: "", note: "" }}
      busy={false}
      onChange={noop}
      onSubmit={noop}
    />,
  );

describe("CallCapacityCard", () => {
  it("renders nothing while voice is off", () => {
    expect(renderToStaticMarkup(<CallCapacityCard voiceEnabled={false} />)).toBe("");
  });

  it("shows the range error and disables Send for an out-of-range peak", () => {
    const html = fields("501");
    expect(html).toContain(c.errorPeak.replace("{max}", "500"));
    expect(html).toMatch(/<button type="submit"[^>]*disabled/);
  });

  it("asks for a location when none is ticked", () => {
    expect(fields("20", [])).toContain(c.errorLocations);
  });

  it("enables Send for a valid request", () => {
    const html = fields("20");
    expect(html).not.toContain(c.errorPeak.replace("{max}", "500"));
    expect(html).not.toMatch(/<button type="submit"[^>]*disabled/);
    expect(html).toContain("Downtown");
  });
});
