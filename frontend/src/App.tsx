import { Suspense, lazy, useState } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import ExternalRedirect from "./components/shared/ExternalRedirect";
import Sidebar from "./components/layout/Sidebar";
import TopBar from "./components/layout/TopBar";
import { useAuth } from "./hooks/useAuth";
import Availability from "./pages/employee/Availability";
import CheckIn from "./pages/employee/CheckIn";
import MyPreferences from "./pages/employee/MyPreferences";
import CheckInQr from "./pages/manager/CheckInQr";
import CheckInReport from "./pages/manager/CheckInReport";
import Company from "./pages/manager/Company";
import Dashboard from "./pages/manager/Dashboard";
import Employees from "./pages/manager/Employees";
import Locations from "./pages/manager/Locations";
import Payroll from "./pages/manager/Payroll";
import Regions from "./pages/manager/Regions";
import RoleEquivalents from "./pages/manager/RoleEquivalents";
import Roles from "./pages/manager/Roles";
import Schedule from "./pages/manager/Schedule";
import EmployeeAssociation from "./pages/manager/EmployeeAssociation";
import EmployeeAvailability from "./pages/manager/EmployeeAvailability";
import EmployeeOnboarding from "./pages/manager/EmployeeOnboarding";
import DayBlackouts from "./pages/manager/DayBlackouts";
import DayPreferences from "./pages/manager/DayPreferences";
import ExportSchedules from "./pages/manager/ExportSchedules";
import ApprovedSchedules from "./pages/manager/ApprovedSchedules";
import FrequencyCaps from "./pages/manager/FrequencyCaps";
import HourRangePreferences from "./pages/manager/HourRangePreferences";
import HourRestrictions from "./pages/manager/HourRestrictions";
import ShiftTemplates from "./pages/manager/ShiftTemplates";
import SpecialHours from "./pages/manager/SpecialHours";
import Team from "./pages/manager/Team";
import QuickEntry from "./pages/manager/QuickEntry";
import PreferenceReviews from "./pages/manager/PreferenceReviews";
import ManagerDataPrivacy from "./pages/manager/DataPrivacy";
import EmployeeDataPrivacy from "./pages/employee/DataPrivacy";
import CancellationBanner from "./components/shared/CancellationBanner";
import RestrictedLocationsBadge from "./components/shared/RestrictedLocationsBadge";
import { isAdminOnlyPath } from "./utils/permissions";

const Login = lazy(() => import("./pages/Login"));
const Register = lazy(() => import("./pages/Register"));
const AcceptInvite = lazy(() => import("./pages/AcceptInvite"));
const AcceptManagerInvite = lazy(() => import("./pages/AcceptManagerInvite"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const VerifyEmail = lazy(() => import("./pages/VerifyEmail"));

function ProtectedLayout() {
  const { user, loading } = useAuth();
  // Below md the sidebar is a drawer (#215); this is whether it is showing.
  const [navOpen, setNavOpen] = useState(false);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-400">Loading...</div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      {/* min-w-0 lets this column shrink below its content's width, so a
          wide table scrolls inside <main> instead of widening the page. */}
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar onMenuClick={() => setNavOpen(true)} />
        <CancellationBanner />
        <main className="flex-1 p-4 md:p-6 overflow-x-auto">
          <RestrictedLocationsBadge />
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ManagerGuard() {
  const { user, isAdmin } = useAuth();
  const { pathname } = useLocation();
  if (user && user.user_role !== "manager") {
    return <Navigate to="/employee/availability" replace />;
  }
  // Company-wide pages are admin-only (#149); a regular manager who
  // follows an old link lands on the dashboard instead.
  if (user && !isAdmin && isAdminOnlyPath(pathname)) {
    return <Navigate to="/manager/dashboard" replace />;
  }
  return <Outlet />;
}

export default function App() {
  const { user, loading } = useAuth();

  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-newsprint">
          <span className="font-data text-xs uppercase tracking-[0.14em] text-ink/50">
            Loading
          </span>
        </div>
      }
    >
      <Routes>
        {/* wizscheduler.com owns marketing content and the legal pages now
            (see docs/superpowers/specs/2026-09-22-marketing-site-design.md). */}
        <Route
          path="/features"
          element={<ExternalRedirect to="https://wizscheduler.com/features" />}
        />

        {/* Public routes */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/accept-manager-invite" element={<AcceptManagerInvite />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/verify-email" element={<VerifyEmail />} />

        {/* Protected routes */}
        <Route element={<ProtectedLayout />}>
          {/* Manager routes */}
          <Route path="/manager" element={<ManagerGuard />}>
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="company" element={<Company />} />
            <Route path="regions" element={<Regions />} />
            <Route path="locations" element={<Locations />} />
            <Route path="roles" element={<Roles />} />
            <Route path="role-equivalents" element={<RoleEquivalents />} />
            <Route path="employees" element={<Employees />} />
            <Route path="quick-entry" element={<QuickEntry />} />
            <Route path="team" element={<Team />} />
            <Route path="hour-restrictions" element={<HourRestrictions />} />
            <Route path="day-blackouts" element={<DayBlackouts />} />
            <Route path="day-preferences" element={<DayPreferences />} />
            <Route path="hour-range-preferences" element={<HourRangePreferences />} />
            <Route path="frequency-caps" element={<FrequencyCaps />} />
            <Route path="preference-reviews" element={<PreferenceReviews />} />
            <Route path="employee-onboarding" element={<EmployeeOnboarding />} />
            <Route path="employee-availability" element={<EmployeeAvailability />} />
            <Route path="employee-association" element={<EmployeeAssociation />} />
            <Route path="shift-templates" element={<ShiftTemplates />} />
            <Route path="check-in-qr" element={<CheckInQr />} />
            <Route path="check-in-report" element={<CheckInReport />} />
            <Route path="payroll" element={<Payroll />} />
            <Route path="special-hours" element={<SpecialHours />} />
            <Route path="schedule" element={<Schedule />} />
            <Route path="export-schedules" element={<ExportSchedules />} />
            <Route path="approved-schedules" element={<ApprovedSchedules />} />
            <Route path="data-privacy" element={<ManagerDataPrivacy />} />
          </Route>

          {/* Employee routes */}
          <Route path="/employee">
            <Route path="availability" element={<Availability />} />
            <Route path="preferences" element={<MyPreferences />} />
            <Route path="check-in" element={<CheckIn />} />
            <Route path="data-privacy" element={<EmployeeDataPrivacy />} />
          </Route>
        </Route>

        {/* Root: signed-in users go to their dashboard; everyone else goes
            to /login — the marketing home page lives at wizscheduler.com
            now (see docs/superpowers/specs
            2026-09-22-marketing-site-design.md). */}
        <Route
          path="/"
          element={
            loading ? (
              <div className="min-h-screen flex items-center justify-center">
                <div className="text-gray-400">Loading...</div>
              </div>
            ) : user ? (
              user.user_role === "manager" ? (
                <Navigate to="/manager/dashboard" replace />
              ) : (
                <Navigate to="/employee/availability" replace />
              )
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />

        {/* Catch-all redirect */}
        <Route
          path="*"
          element={
            loading ? (
              <div className="min-h-screen flex items-center justify-center">
                <div className="text-gray-400">Loading...</div>
              </div>
            ) : user ? (
              user.user_role === "manager" ? (
                <Navigate to="/manager/dashboard" replace />
              ) : (
                <Navigate to="/employee/availability" replace />
              )
            ) : (
              <Navigate to="/" replace />
            )
          }
        />
      </Routes>
    </Suspense>
  );
}
