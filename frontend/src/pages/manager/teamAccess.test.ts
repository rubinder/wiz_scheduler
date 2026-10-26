/**
 * Team page access edit flow (#149, #150): draft -> toggle -> validate ->
 * PUT /managers/{id} -> inline error. The page's handlers call exactly these
 * functions; the project has no DOM test environment, so the request is
 * checked against a stubbed fetch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { updateManager } from "../../api/managers";
import { createManagerInvite } from "../../api/managerInvites";
import type { ManagerSummary } from "../../types";
import {
  accessErrorMessage,
  draftError,
  draftFromManager,
  hasArchivedAssignments,
  toggleLocation,
  updateBody,
} from "./teamAccess";

const loc = (id: string, archived = false) => ({ id, name: id.toUpperCase(), timezone: "UTC", archived });

const admin: ManagerSummary = {
  id: "m-admin",
  email: "admin@example.com",
  full_name: "Ada Admin",
  manager_type: "admin",
  assigned_locations: [],
};

const regular: ManagerSummary = {
  id: "m-reg",
  email: "reg@example.com",
  full_name: null,
  manager_type: "regular",
  assigned_locations: [loc("a"), loc("old", true)],
};

const messages = { lastAdminError: "Keep one admin" };

describe("team access draft", () => {
  it("starts from the manager's type and active locations", () => {
    expect(draftFromManager(admin)).toEqual({ managerType: "admin", locationIds: [] });
    expect(draftFromManager(regular)).toEqual({ managerType: "regular", locationIds: ["a"] });
  });

  it("toggles a location on and off", () => {
    const d = draftFromManager(regular);
    const added = toggleLocation(d, "b");
    expect(added.locationIds).toEqual(["a", "b"]);
    expect(toggleLocation(added, "a").locationIds).toEqual(["b"]);
    expect(d.locationIds).toEqual(["a"]);
  });

  it("requires a location for a regular manager only", () => {
    expect(draftError({ managerType: "regular", locationIds: [] })).toBe("locationsRequired");
    expect(draftError({ managerType: "regular", locationIds: ["a"] })).toBeNull();
    expect(draftError({ managerType: "admin", locationIds: [] })).toBeNull();
  });

  it("flags archived assignments a replace would drop", () => {
    expect(hasArchivedAssignments(regular)).toBe(true);
    expect(hasArchivedAssignments(admin)).toBe(false);
  });
});

describe("update body", () => {
  it("promotes to admin with the type only", () => {
    expect(updateBody(regular, { managerType: "admin", locationIds: ["a"] })).toEqual({
      manager_type: "admin",
    });
  });

  it("demotes an admin with the chosen locations", () => {
    expect(updateBody(admin, { managerType: "regular", locationIds: ["a", "b"] })).toEqual({
      manager_type: "regular",
      location_ids: ["a", "b"],
    });
  });

  it("replaces a regular manager's set when it changed", () => {
    expect(updateBody(regular, { managerType: "regular", locationIds: ["b"] })).toEqual({
      manager_type: "regular",
      location_ids: ["b"],
    });
  });

  it("omits an unchanged set so archived assignments survive", () => {
    expect(updateBody(regular, { managerType: "regular", locationIds: ["a"] })).toEqual({
      manager_type: "regular",
    });
  });
});

describe("saving through the API", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    const store: Record<string, string> = { token: "tok" };
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => (store[k] = v),
      removeItem: (k: string) => delete store[k],
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  const respond = (status: number, body: unknown) =>
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );

  it("PUTs the body to /managers/{id} and returns the row", async () => {
    const saved = { ...admin, manager_type: "regular", assigned_locations: [loc("a")] };
    respond(200, saved);
    const body = updateBody(admin, { managerType: "regular", locationIds: ["a"] });
    await expect(updateManager(admin.id, body)).resolves.toEqual(saved);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/managers/m-admin");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ manager_type: "regular", location_ids: ["a"] });
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("shows the last-admin message for a 409", async () => {
    respond(409, { detail: "The company must keep at least one admin manager" });
    const err = await updateManager(admin.id, { manager_type: "regular", location_ids: ["a"] }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(accessErrorMessage(err, messages, "fallback")).toBe("Keep one admin");
  });

  it("shows the server's reason for a 400", async () => {
    respond(400, { detail: "Location x is not an active location of this company" });
    const err = await updateManager(regular.id, { manager_type: "regular", location_ids: ["x"] }).catch(
      (e: unknown) => e,
    );
    expect(accessErrorMessage(err, messages, "fallback")).toBe(
      "Location x is not an active location of this company",
    );
  });

  it("sends the type and locations with a regular-manager invite", async () => {
    respond(200, {});
    await createManagerInvite("new@example.com", "regular", ["a", "b"]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      email: "new@example.com",
      manager_type: "regular",
      location_ids: ["a", "b"],
    });
  });

  it("sends no locations with an admin invite", async () => {
    respond(200, {});
    await createManagerInvite("boss@example.com", "admin", ["a"]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      email: "boss@example.com",
      manager_type: "admin",
    });
  });
});
