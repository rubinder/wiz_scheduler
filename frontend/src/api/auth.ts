import type { LoginRequest, RegisterRequest, TokenResponse, User } from "../types";
import { apiFetch } from "./client";

export function register(body: RegisterRequest): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
