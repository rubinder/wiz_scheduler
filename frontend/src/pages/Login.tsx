import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../hooks/useAuth";

interface OwnershipGroupOption {
  id: string;
  name: string;
}

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Ownership group selection state
  const [ownershipGroups, setOwnershipGroups] = useState<
    OwnershipGroupOption[] | null
  >(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    setOwnershipGroups(null);
    try {
      await login(email, password);
      navigate("/manager/dashboard");
    } catch (err: unknown) {
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        typeof err.data === "object" &&
        err.data !== null &&
        (err.data as Record<string, unknown>).code ===
          "multiple_ownership_groups"
      ) {
        const groups = (err.data as Record<string, unknown>)
          .groups as OwnershipGroupOption[];
        setOwnershipGroups(groups);
      } else {
        setError(err instanceof Error ? err.message : "Login failed");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSelectGroup = async (groupId: string) => {
    setError("");
    setLoading(true);
    try {
      await login(email, password, groupId);
      navigate("/manager/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded-lg shadow-md w-full max-w-md">
        <h1 className="text-2xl font-bold text-center mb-6 flex items-center justify-center gap-2">
          Sign in to Wiz Scheduler <img src="/favicon.svg" alt="" className="w-8 h-8 inline" />
        </h1>
        {error && (
          <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
            {error}
          </div>
        )}

        {/* Ownership group selection */}
        {ownershipGroups && (
          <div className="mb-4">
            <div className="p-4 bg-indigo-50 rounded-lg border border-indigo-200">
              <p className="text-sm font-medium text-gray-700 mb-3">
                Your account is associated with multiple organizations. Please
                select which one to sign in to:
              </p>
              <div className="space-y-2">
                {ownershipGroups.map((group) => (
                  <button
                    key={group.id}
                    onClick={() => handleSelectGroup(group.id)}
                    disabled={loading}
                    className="w-full text-left px-4 py-3 bg-white border border-gray-200 rounded-lg hover:border-indigo-400 hover:bg-indigo-50 transition-colors disabled:opacity-50"
                  >
                    <span className="text-sm font-medium text-gray-900">
                      {group.name}
                    </span>
                  </button>
                ))}
              </div>
              <button
                onClick={() => setOwnershipGroups(null)}
                className="mt-3 text-xs text-gray-500 hover:text-gray-700"
              >
                Back to login
              </button>
            </div>
          </div>
        )}

        {/* Login form — hidden when choosing ownership group */}
        {!ownershipGroups && (
          <>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-indigo-600 text-white py-2 rounded hover:bg-indigo-700 disabled:opacity-50 font-medium"
              >
                {loading ? "Signing in..." : "Sign In"}
              </button>
            </form>
            <p className="mt-4 text-center text-sm text-gray-600">
              Don't have an account?{" "}
              <Link to="/register" className="text-indigo-600 hover:underline">
                Register
              </Link>
            </p>
            <div className="mt-6 p-4 bg-gray-50 rounded-lg border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Demo Credentials
              </p>
              <button
                type="button"
                onClick={() => {
                  setEmail("abc@example.com");
                  setPassword("example");
                }}
                className="w-full text-left text-sm text-gray-700 hover:bg-gray-100 rounded p-2 transition-colors"
              >
                <span className="font-medium">Manager:</span> abc@example.com / example
              </button>
              <button
                type="button"
                onClick={() => {
                  setEmail("employee1@example.com");
                  setPassword("example");
                }}
                className="w-full text-left text-sm text-gray-700 hover:bg-gray-100 rounded p-2 transition-colors"
              >
                <span className="font-medium">Employee:</span> employee1@example.com / example
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
