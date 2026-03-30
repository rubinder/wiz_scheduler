import type {
  AcceptInviteResponse,
  BulkUploadResponse,
  Employee,
  EmployeeAvailability,
  EmployeeMeProfile,
  InviteInfoResponse,
  InviteResponse,
  InviteStatusResponse,
} from "../types";
import { apiFetch } from "./client";

export function getMyProfile(): Promise<EmployeeMeProfile> {
  return apiFetch<EmployeeMeProfile>("/employees/me");
}

export function listEmployees(): Promise<Employee[]> {
  return apiFetch<Employee[]>("/employees/");
}

export function createEmployee(body: {
  full_name: string;
  email?: string | null;
  user_id?: string | null;
  location_ids?: string[] | null;
  roles?: { role_id: string; skill_level: number }[] | null;
  company_ids?: string[];
}): Promise<Employee> {
  return apiFetch<Employee>("/employees/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateEmployee(
  id: string,
  body: {
    full_name?: string;
    email?: string | null;
    user_id?: string | null;
    location_ids?: string[] | null;
    roles?: { role_id: string; skill_level: number }[] | null;
    company_ids?: string[];
  }
): Promise<Employee> {
  return apiFetch<Employee>(`/employees/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteEmployee(id: string): Promise<void> {
  return apiFetch<void>(`/employees/${id}`, { method: "DELETE" });
}

export async function bulkUploadEmployees(
  file: File
): Promise<BulkUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<BulkUploadResponse>("/employees/bulk-upload", {
    method: "POST",
    body: formData as unknown as BodyInit,
  });
}

// ── Availability ──

export function listAllAvailability(weekStart?: string): Promise<EmployeeAvailability[]> {
  const qs = weekStart ? `?week_start=${weekStart}` : "";
  return apiFetch<EmployeeAvailability[]>(`/employees/availability/all${qs}`);
}

export function listAvailability(
  employeeId: string
): Promise<EmployeeAvailability[]> {
  return apiFetch<EmployeeAvailability[]>(
    `/employees/${employeeId}/availability`
  );
}

export function createAvailability(body: {
  employee_id: string;
  year: number;
  month: number;
  day: number;
  start_time: string;
  end_time: string;
}): Promise<EmployeeAvailability> {
  return apiFetch<EmployeeAvailability>("/employees/availability", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteAvailability(id: string): Promise<void> {
  return apiFetch<void>(`/employees/availability/${id}`, {
    method: "DELETE",
  });
}

// ── Invites ──

export function sendInvite(employeeId: string): Promise<InviteResponse> {
  return apiFetch<InviteResponse>(`/employees/${employeeId}/invite`, {
    method: "POST",
  });
}

export function getInviteInfo(token: string): Promise<InviteInfoResponse> {
  return apiFetch<InviteInfoResponse>(`/invites/${token}`);
}

export function acceptInvite(
  token: string,
  password: string
): Promise<AcceptInviteResponse> {
  return apiFetch<AcceptInviteResponse>(`/invites/${token}/accept`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function listInvites(): Promise<InviteStatusResponse[]> {
  return apiFetch<InviteStatusResponse[]>("/employees/invites");
}
