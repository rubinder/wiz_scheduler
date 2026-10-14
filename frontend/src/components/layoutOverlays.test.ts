/**
 * Full-screen overlays opened from the layout chrome must portal to <body>.
 *
 * The TopBar and Sidebar use `backdrop-blur-*`. A backdrop filter makes an
 * element the containing block for its `position: fixed` descendants, so a
 * `fixed inset-0` modal rendered inside the TopBar is sized to the 56px header,
 * and the page's own glass cards paint over it. The feedback form's submit
 * button ended up hidden behind the Role Equivalents cards that way.
 *
 * A source check because the project has no DOM test environment.
 */
import { describe, expect, it } from "vitest";

const SOURCES = import.meta.glob(["./layout/*.tsx", "./FeedbackForm.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("layout overlays", () => {
  it("covers the feedback form", () => {
    expect(Object.keys(SOURCES)).toContain("./FeedbackForm.tsx");
  });

  for (const [path, source] of Object.entries(SOURCES)) {
    const overlays = source.match(/\bfixed inset-0\b/g)?.length ?? 0;
    if (overlays === 0) continue;

    it(`${path} portals each fixed overlay to document.body`, () => {
      const portals = source.match(/createPortal\(/g)?.length ?? 0;
      expect(portals).toBeGreaterThanOrEqual(overlays);
      expect(source).toMatch(/document\.body/);
    });
  }
});
