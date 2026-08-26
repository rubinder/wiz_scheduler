import { Suspense, lazy } from "react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import Sidebar from "./components/layout/Sidebar";
import TopBar from "./components/layout/TopBar";
import { useAuth } from "./hooks/useAuth";
import Availability from "./pages/employee/Availability";
import CheckIn from "./pages/employee/CheckIn";
import CheckInQr from "./pages/manager/CheckInQr";
import CheckInReport from "./pages/manager/CheckInReport";
import Company from "./pages/manager/Company";
import Dashboard from "./pages/manager/Dashboard";
import Employees from "./pages/manager/Employees";
import Locations from "./pages/manager/Locations";
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
import ManagerDataPrivacy from "./pages/manager/DataPrivacy";
import EmployeeDataPrivacy from "./pages/employee/DataPrivacy";
import CancellationBanner from "./components/shared/CancellationBanner";

const Login = lazy(() => import("./pages/Login"));
const Register = lazy(() => import("./pages/Register"));
const AcceptInvite = lazy(() => import("./pages/AcceptInvite"));
const AcceptManagerInvite = lazy(() => import("./pages/AcceptManagerInvite"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const PrivacyPolicy = lazy(() => import("./pages/PrivacyPolicy"));
const TermsOfService = lazy(() => import("./pages/TermsOfService"));
const DataProcessingAgreement = lazy(() => import("./pages/DataProcessingAgreement"));
const Landing = lazy(() => import("./pages/Landing"));
const Features = lazy(() => import("./pages/Features"));

function ProtectedLayout() {
  const { user, loading } = useAuth();

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
      <Sidebar />
      <div className="flex-1 flex flex-col">
        <TopBar />
        <CancellationBanner />
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ManagerGuard() {
  const { user } = useAuth();
  if (user && user.user_role !== "manager") {
    return <Navigate to="/employee/availability" replace />;
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
        {/* Public routes */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/accept-manager-invite" element={<AcceptManagerInvite />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/privacy-policy" element={<PrivacyPolicy />} />
        <Route path="/terms" element={<TermsOfService />} />
        <Route path="/dpa" element={<DataProcessingAgreement />} />
        <Route path="/features" element={<Features />} />

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
            <Route path="team" element={<Team />} />
            <Route path="hour-restrictions" element={<HourRestrictions />} />
            <Route path="day-blackouts" element={<DayBlackouts />} />
            <Route path="day-preferences" element={<DayPreferences />} />
            <Route path="hour-range-preferences" element={<HourRangePreferences />} />
            <Route path="frequency-caps" element={<FrequencyCaps />} />
            <Route path="employee-onboarding" element={<EmployeeOnboarding />} />
            <Route path="employee-availability" element={<EmployeeAvailability />} />
            <Route path="employee-association" element={<EmployeeAssociation />} />
            <Route path="shift-templates" element={<ShiftTemplates />} />
            <Route path="check-in-qr" element={<CheckInQr />} />
            <Route path="check-in-report" element={<CheckInReport />} />
            <Route path="special-hours" element={<SpecialHours />} />
            <Route path="schedule" element={<Schedule />} />
            <Route path="export-schedules" element={<ExportSchedules />} />
            <Route path="approved-schedules" element={<ApprovedSchedules />} />
            <Route path="data-privacy" element={<ManagerDataPrivacy />} />
          </Route>

          {/* Employee routes */}
          <Route path="/employee">
            <Route path="availability" element={<Availability />} />
            <Route path="check-in" element={<CheckIn />} />
            <Route path="data-privacy" element={<EmployeeDataPrivacy />} />
          </Route>
        </Route>

        {/* Landing page for unauthenticated users */}
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
              <Landing />
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
