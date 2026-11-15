import { Navigate, Route, Routes } from 'react-router-dom'

import { AdminLayout, PublicLayout } from './components/layout'
import NotFound from './pages/NotFound'
import Login from './pages/admin/Login'
import Audit from './pages/admin/Audit'
import Customers from './pages/admin/Customers'
import Dashboard from './pages/admin/Dashboard'
import KeyDetail from './pages/admin/KeyDetail'
import KeyNew from './pages/admin/KeyNew'
import Keys from './pages/admin/Keys'
import LicenseDetail from './pages/admin/LicenseDetail'
import Licenses from './pages/admin/Licenses'
import Orders from './pages/admin/Orders'
import Signing from './pages/admin/Signing'
import Activate from './pages/public/Activate'
import Home from './pages/public/Home'
import Redeem from './pages/public/Redeem'
import Success from './pages/public/Success'
import Upgrade from './pages/public/Upgrade'
import Verify from './pages/public/Verify'

export default function App() {
  return (
    <Routes>
      <Route element={<PublicLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/activate" element={<Activate />} />
        <Route path="/verify" element={<Verify />} />
        <Route path="/success" element={<Success />} />
        <Route path="/upgrade" element={<Upgrade />} />
        <Route path="/redeem" element={<Redeem />} />
        <Route path="*" element={<NotFound />} />
      </Route>

      <Route path="/admin/login" element={<Login />} />

      <Route path="/admin" element={<AdminLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="licenses" element={<Licenses />} />
        <Route path="licenses/:serial" element={<LicenseDetail />} />
        <Route path="keys" element={<Keys />} />
        <Route path="keys/new" element={<KeyNew />} />
        <Route path="keys/:id" element={<KeyDetail />} />
        <Route path="customers" element={<Customers />} />
        <Route path="orders" element={<Orders />} />
        <Route path="audit" element={<Audit />} />
        <Route path="signing" element={<Signing />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Route>
    </Routes>
  )
}
