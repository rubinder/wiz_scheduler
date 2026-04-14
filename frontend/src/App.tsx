import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import Sidebar from "./components/layout/Sidebar";
import TopBar from "./components/layout/TopBar";
import { useAuth } from "./hooks/useAuth";
import AcceptInvite from "./pages/AcceptInvite";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Availability from "./pages/employee/Availability";
import Company from "./pages/manager/Company";
import Dashboard from "./pages/manager/Dashboard";
import Employees from "./pages/manager/Employees";
import Locations from "./pages/manager/Locations";
import Regions from "./pages/manager/Regions";
import RoleEquivalents from "./pages/manager/RoleEquivalents";
import Roles from "./pages/manager/Roles";
import Schedule from "./pages/manager/Schedule";
import EmployeeAssociation from "./pages/manager/EmployeeAssociation";
import EmployeeOnboarding from "./pages/manager/EmployeeOnboarding";
import DayBlackouts from "./pages/manager/DayBlackouts";
import ExportSchedules from "./pages/manager/ExportSchedules";
import HourRestrictions from "./pages/manager/HourRestrictions";
import ShiftTemplates from "./pages/manager/ShiftTemplates";
import PrivacyPolicy from "./pages/PrivacyPolicy";
import TermsOfService from "./pages/TermsOfService";
import DataProcessingAgreement from "./pages/DataProcessingAgreement";
import ManagerDataPrivacy from "./pages/manager/DataPrivacy";
import EmployeeDataPrivacy from "./pages/employee/DataPrivacy";
import Landing from "./pages/Landing";

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
    <Routes>
      {/* Public routes */}
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route path="/privacy-policy" element={<PrivacyPolicy />} />
      <Route path="/terms" element={<TermsOfService />} />
      <Route path="/dpa" element={<DataProcessingAgreement />} />

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
          <Route path="hour-restrictions" element={<HourRestrictions />} />
          <Route path="day-blackouts" element={<DayBlackouts />} />
          <Route path="employee-onboarding" element={<EmployeeOnboarding />} />
          <Route path="employee-association" element={<EmployeeAssociation />} />
          <Route path="shift-templates" element={<ShiftTemplates />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="export-schedules" element={<ExportSchedules />} />
          <Route path="data-privacy" element={<ManagerDataPrivacy />} />
        </Route>

        {/* Employee routes */}
        <Route path="/employee">
          <Route path="availability" element={<Availability />} />
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
  );
}
