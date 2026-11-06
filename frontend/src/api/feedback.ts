import { apiFetch } from "./client";

export type FeedbackType = "bug" | "feature" | "feedback" | "other";

export interface FeedbackSubmission {
  subject: string;
  message: string;
  type: FeedbackType;
}

/** Mirrors backend/routers/feedback.py FeedbackResponse. */
export interface FeedbackResponse {
  success: boolean;
  message: string;
}

/**
 * POST /feedback/submit. Resolves only when the feedback was delivered.
 * The backend answers 200 with success=false when email is not configured;
 * that is a failure to the caller, so it is thrown here as an Error carrying
 * the backend's message.
 */
export async function submitFeedback(body: FeedbackSubmission): Promise<FeedbackResponse> {
  const res = await apiFetch<FeedbackResponse>("/feedback/submit", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.success) {
    throw new Error(res.message || "Failed to submit feedback");
  }
  return res;
}
