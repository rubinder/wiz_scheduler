import type {
  CompanySummary,
  OwnershipGroupCompaniesResponse,
} from "../types";
import { apiFetch } from "./client";

export function getOwnershipGroup(): Promise<OwnershipGroupCompaniesResponse> {
  return apiFetch<OwnershipGroupCompaniesResponse>("/ownership-group/");
}

export function addCompanyToGroup(body: {
  name: string;
}): Promise<CompanySummary> {
  return apiFetch<CompanySummary>("/ownership-group/companies", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
