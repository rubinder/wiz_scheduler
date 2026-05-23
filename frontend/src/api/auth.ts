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
