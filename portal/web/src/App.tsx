import { Link, Route, Routes } from 'react-router'
import { Shell } from './components/Shell'
import { Cabinet } from './pages/Cabinet'
import { InvoicePage } from './pages/InvoicePage'
import { Login } from './pages/Login'
import { MockCheckout } from './pages/MockCheckout'
import { Register } from './pages/Register'
import { Reset } from './pages/Reset'
import { Verify } from './pages/Verify'
import { ApplicationView } from './pages/staff/ApplicationView'
import { Applications } from './pages/staff/Applications'
import { AuditPage } from './pages/staff/AuditPage'
import { LocalAccountPage } from './pages/staff/LocalAccount'
import { StaffLogin } from './pages/staff/StaffLogin'

function NotFound() {
  return (
    <Shell>
      <h1 className="text-[22px] font-extrabold text-ink">Страница не найдена</h1>
      <Link to="/" className="mt-3 inline-block font-semibold text-accent hover:text-accent-hover">
        На главную
      </Link>
    </Shell>
  )
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Cabinet />} />
      <Route path="/cabinet" element={<Cabinet />} />
      <Route path="/register" element={<Register />} />
      <Route path="/verify" element={<Verify />} />
      <Route path="/login" element={<Login />} />
      <Route path="/reset" element={<Reset />} />
      <Route path="/invoice" element={<InvoicePage />} />
      <Route path="/invoice/:number" element={<InvoicePage />} />
      <Route path="/pay/mock/:id" element={<MockCheckout />} />
      <Route path="/staff/login" element={<StaffLogin />} />
      <Route path="/staff" element={<Applications />} />
      <Route path="/staff/applications/:id" element={<ApplicationView />} />
      <Route path="/staff/applications/:id/invoice" element={<InvoicePage />} />
      <Route path="/staff/applications/:id/invoices/:number" element={<InvoicePage />} />
      <Route path="/staff/local" element={<LocalAccountPage />} />
      <Route path="/staff/audit" element={<AuditPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
