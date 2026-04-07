import { apiFetch } from "./client";

export interface ConsentRecord {
  id: string;
  consent_type: string;
  version: string;
  granted_at: string;
  revoked_at: string | null;
}

export interface DataExport {
  exported_at: string;
  user: Record<string, unknown>;
  employee: Record<string, unknown> | null;
  availability: Record<string, unknown>[];
  affinities: Record<string, unknown>[];
  shifts: Record<string, unknown>[];
  consents: ConsentRecord[];
}

export interface PolicyDocument {
  version: string;
  effective_date: string;
  content: string;
  processors?: { name: string; purpose: string; location: string }[];
}

export async function exportMyData(): Promise<DataExport> {
  return apiFetch<DataExport>("/gdpr/export");
}

export async function deleteMyAccount(): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>("/gdpr/delete-account", { method: "DELETE" });
}

export async function getConsents(): Promise<ConsentRecord[]> {
  return apiFetch<ConsentRecord[]>("/gdpr/consents");
}

export async function getPrivacyPolicy(): Promise<PolicyDocument> {
  return apiFetch<PolicyDocument>("/gdpr/privacy-policy");
}

export async function getTermsOfService(): Promise<PolicyDocument> {
  return apiFetch<PolicyDocument>("/gdpr/terms");
}

export async function getDpa(): Promise<PolicyDocument> {
  return apiFetch<PolicyDocument>("/gdpr/dpa");
}
