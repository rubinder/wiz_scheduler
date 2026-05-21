import { apiFetch } from "./client";

export interface GoogleAuthResponse {
  access_token: string | null;
  token_type: string;
  link_required: boolean;
  email: string | null;
  google_name: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export function googleAuth(idToken: string): Promise<GoogleAuthResponse> {
  return apiFetch<GoogleAuthResponse>("/auth/google", {
    method: "POST",
    body: JSON.stringify({ id_token: idToken }),
  });
}

export function googleLink(
  idToken: string,
  email: string,
  password: string,
  ownershipGroupId?: string,
): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/google/link", {
    method: "POST",
    body: JSON.stringify({
      id_token: idToken,
      email,
      password,
      ownership_group_id: ownershipGroupId || null,
    }),
  });
}

/**
 * Link Google to the currently-authenticated user (no password re-prompt;
 * the bearer JWT is the proof of identity). Used by the in-app Link Google
 * card on the manager dashboard.
 */
export function googleLinkCurrent(idToken: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/google/link-current", {
    method: "POST",
    body: JSON.stringify({ id_token: idToken }),
  });
}
