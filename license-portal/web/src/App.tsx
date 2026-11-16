import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { AdminLayout, PublicLayout } from './components/layout'
import { LoadingScreen } from './components/ui'
import NotFound from './pages/NotFound'
import Activate from './pages/public/Activate'
import Home from './pages/public/Home'
import Redeem from './pages/public/Redeem'
import Success from './pages/public/Success'
import Upgrade from './pages/public/Upgrade'
import Verify from './pages/public/Verify'

const Login = lazy(() => import('./pages/admin/Login'))
const Dashboard = lazy(() => import('./pages/admin/Dashboard'))
const Licenses = lazy(() => import('./pages/admin/Licenses'))
const LicenseDetail = lazy(() => import('./pages/admin/LicenseDetail'))
const Keys = lazy(() => import('./pages/admin/Keys'))
const KeyNew = lazy(() => import('./pages/admin/KeyNew'))
const KeyDetail = lazy(() => import('./pages/admin/KeyDetail'))
const Customers = lazy(() => import('./pages/admin/Customers'))
const CustomerDetail = lazy(() => import('./pages/admin/CustomerDetail'))
const Orders = lazy(() => import('./pages/admin/Orders'))
const Audit = lazy(() => import('./pages/admin/Audit'))
const Signing = lazy(() => import('./pages/admin/Signing'))

export default function App() {
  return (
    <Suspense fallback={<LoadingScreen text="正在加载页面…" />}>
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
          <Route path="customers/:id" element={<CustomerDetail />} />
          <Route path="orders" element={<Orders />} />
          <Route path="audit" element={<Audit />} />
          <Route path="signing" element={<Signing />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
