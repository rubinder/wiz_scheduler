import type { LoginRequest, RegisterRequest, TokenResponse, User } from "../types";
import { apiFetch } from "./client";

export function register(body: RegisterRequest): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createCheckoutSession(
  email: string
): Promise<{ session_id: string; url: string }> {
  return apiFetch<{ session_id: string; url: string }>(
    "/billing/create-checkout-session",
    {
      method: "POST",
      body: JSON.stringify({ email }),
    }
  );
}

export function login(body: LoginRequest): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getMe(): Promise<User> {
  return apiFetch<User>("/auth/me");
}

export function switchCompany(companyId: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/switch-company", {
    method: "POST",
    body: JSON.stringify({ company_id: companyId }),
  });
}

export function forgotPassword(email: string): Promise<void> {
  return apiFetch<void>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(
  token: string,
  newPassword: string,
): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

/**
 * Redeem a verification token. Returns a session, so the link doubles as a
 * login for someone who opened the email on a different device.
 */
export function verifyEmail(token: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

/**
 * Request a fresh verification link. Always resolves — the endpoint is
 * deliberately silent about whether the address exists or is already
 * verified, so never present the result as confirmation that mail was sent
 * to a real account.
 */
export function resendVerification(email: string): Promise<void> {
  return apiFetch<void>("/auth/resend-verification", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}
