import type { Company } from "../types";
import { apiFetch } from "./client";

export function getCompany(): Promise<Company> {
  return apiFetch<Company>("/company/");
}

export function listGroupCompanies(): Promise<Company[]> {
  return apiFetch<Company[]>("/company/all");
}

export function updateCompany(body: { name?: string }): Promise<Company> {
  return apiFetch<Company>("/company/", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
