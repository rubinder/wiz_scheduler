import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import * as authApi from "../api/auth";
import { getDeviceId } from "../utils/deviceId";
import type { User } from "../types";
import { managerAccess } from "../utils/permissions";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string, ownershipGroupId?: string) => Promise<void>;
  register: (params: {
    email: string;
    password?: string;
    googleIdToken?: string;
    fullName: string;
    companyName: string;
    privacyAccepted?: boolean;
    termsAccepted?: boolean;
    stripeSessionId?: string;
  }) => Promise<void>;
  logout: () => void;
  switchCompany: (companyId: string) => Promise<void>;
  /** Re-fetch /auth/me. Used after email verification flips email_verified,
   * and after an admin changes their own manager type. */
  refreshUser: () => Promise<void>;
  /** Full company access; false for regular (location-scoped) managers
   * and for non-managers. Derived from /auth/me — see utils/permissions. */
  isAdmin: boolean;
  /** Locations a regular manager may use; null means every location. */
  accessibleLocationIds: string[] | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchMe = useCallback(async () => {
    try {
      const me = await authApi.getMe();
      setUser(me);
    } catch {
      localStorage.removeItem("token");
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (token) {
      fetchMe();
    } else {
      setLoading(false);
    }
  }, [fetchMe]);

  const login = useCallback(
    async (email: string, password: string, ownershipGroupId?: string) => {
      const res = await authApi.login({
        email,
        password,
        ...(ownershipGroupId ? { ownership_group_id: ownershipGroupId } : {}),
      });
      localStorage.setItem("token", res.access_token);
      await fetchMe();
    },
    [fetchMe]
  );

  const register = useCallback(
    async (params: {
      email: string;
      password?: string;
      googleIdToken?: string;
      fullName: string;
      companyName: string;
      privacyAccepted?: boolean;
      termsAccepted?: boolean;
      stripeSessionId?: string;
    }) => {
      const res = await authApi.register({
        email: params.email,
        password: params.password,
        google_id_token: params.googleIdToken,
        full_name: params.fullName,
        company_name: params.companyName,
        privacy_accepted: params.privacyAccepted,
        terms_accepted: params.termsAccepted,
        stripe_session_id: params.stripeSessionId,
        // Observe-only anti-abuse signal; absent when storage is blocked.
        device_id: getDeviceId(),
      });
      localStorage.setItem("token", res.access_token);
      await fetchMe();
    },
    [fetchMe]
  );

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    setUser(null);
  }, []);

  const switchCompany = useCallback(
    async (companyId: string) => {
      const res = await authApi.switchCompany(companyId);
      localStorage.setItem("token", res.access_token);
      await fetchMe();
    },
    [fetchMe]
  );

  const access = useMemo(() => managerAccess(user), [user]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        register,
        logout,
        switchCompany,
        refreshUser: fetchMe,
        isAdmin: access.isAdmin,
        accessibleLocationIds: access.accessibleLocationIds,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
