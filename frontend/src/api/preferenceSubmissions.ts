import type {
  ApprovedPayload,
  Company,
  Coworker,
  MySubmissionState,
  PreferenceReviewListItem,
  PreferenceSubmissionDetail,
  PreferenceSubmissionPayload,
} from "../types";
import { apiFetch } from "./client";

/** Window event fired when an admin flips the company switch, so the
 * sidebar can show or hide the preference pages without a reload. */
export const PREFERENCE_SUBMISSIONS_CHANGED = "wiz:preference-submissions-changed";

/** 403 detail code for a submit while the company switch is off. */
export const DISABLED_CODE = "employee_preference_submissions_disabled";
/** 409 detail code for approving or denying a decided submission. */
export const ALREADY_DECIDED_CODE = "submission_already_decided";

// ── Employee ──

export function getMyPreferences(): Promise<MySubmissionState> {
  return apiFetch<MySubmissionState>("/preference-submissions/me");
}

export function submitMyPreferences(body: PreferenceSubmissionPayload): Promise<MySubmissionState> {
  return apiFetch<MySubmissionState>("/preference-submissions/me", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function listMyCoworkers(): Promise<Coworker[]> {
  return apiFetch<Coworker[]>("/preference-submissions/me/coworkers");
}

// ── Manager ──

export function listSubmissions(): Promise<PreferenceReviewListItem[]> {
  return apiFetch<PreferenceReviewListItem[]>("/preference-submissions/");
}

export function getSubmission(id: string): Promise<PreferenceSubmissionDetail> {
  return apiFetch<PreferenceSubmissionDetail>(`/preference-submissions/${encodeURIComponent(id)}`);
}

export function approveSubmission(
  id: string,
  body: ApprovedPayload,
): Promise<PreferenceSubmissionDetail> {
  return apiFetch<PreferenceSubmissionDetail>(
    `/preference-submissions/${encodeURIComponent(id)}/approve`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function denySubmission(
  id: string,
  reason: string | null,
): Promise<PreferenceSubmissionDetail> {
  return apiFetch<PreferenceSubmissionDetail>(
    `/preference-submissions/${encodeURIComponent(id)}/deny`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

// ── Admin ──

export function setEmployeePreferenceSubmissions(enabled: boolean): Promise<Company> {
  return apiFetch<Company>("/company/settings/employee-preference-submissions", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}
