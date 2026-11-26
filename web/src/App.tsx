import { useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router'
import { RotateCw, ServerCrash } from 'lucide-react'
import { setNavigator } from './api/client'
import { useBrand } from './brand'
import { AuthProvider, useAuth } from './hooks/useAuth'
import { Layout, Wordmark } from './components/Layout'
import { ToastHost } from './components/ToastHost'
import { Button, Spinner } from './components/ui'
import { Setup } from './pages/Setup/Setup'
import { Login } from './pages/Login/Login'
import { Dashboard } from './pages/Dashboard/Dashboard'
import { Conversations } from './pages/Conversations/Conversations'
import { Bookings } from './pages/Bookings/Bookings'
import { BotSettingsPage } from './pages/BotSettings/BotSettings'
import { TestChat } from './pages/TestChat/TestChat'
import { Integrations } from './pages/Integrations/Integrations'
import { System } from './pages/System/System'

function NavigatorBridge() {
  const navigate = useNavigate()
  useEffect(() => {
    setNavigator((path) => navigate(path, { replace: true }))
  }, [navigate])
  return null
}

function Shell() {
  const { phase, refresh } = useAuth()
  const brand = useBrand()

  useEffect(() => {
    if (phase !== 'offline') return
    const t = setInterval(() => void refresh(), 10_000)
    return () => clearInterval(t)
  }, [phase, refresh])

  if (phase === 'checking') {
    return (
      <div className="grid h-dvh place-items-center">
        <div className="flex flex-col items-center gap-2">
          <Wordmark className="text-xl" />
          <Spinner className="py-2" />
        </div>
      </div>
    )
  }

  if (phase === 'offline') {
    return (
      <div className="grid h-dvh place-items-center px-6">
        <div role="alert" className="flex max-w-sm flex-col items-center text-center">
          <ServerCrash className="mb-3 size-8 text-ink-3" aria-hidden />
          <h1 className="text-lg font-semibold">Сервер {brand.name} недоступен</h1>
          <p className="mt-1 text-sm text-ink-2">
            {brand.id === 'nox'
              ? 'Возможно, идут технические работы. Проверьте подключение к интернету — страница обновится, когда сервер ответит.'
              : 'Проверьте, что компьютер с ILER включён и подключён к сети. Страница обновится, когда сервер ответит.'}
          </p>
          <Button className="mt-4" onClick={() => void refresh()} icon={<RotateCw className="size-4" aria-hidden />}>
            Проверить снова
          </Button>
        </div>
      </div>
    )
  }

  return (
    <Routes>
      <Route path="/setup" element={<Setup />} />
      <Route path="/login" element={phase === 'ready' ? <Navigate to="/" replace /> : <Login />} />
      {phase === 'ready' ? (
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="conversations/:id?" element={<Conversations />} />
          <Route path="bookings" element={<Bookings />} />
          <Route path="bot" element={<BotSettingsPage />} />
          <Route path="test-chat" element={<TestChat />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="system" element={<System />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      ) : (
        <Route path="*" element={<Spinner className="h-dvh" />} />
      )}
    </Routes>
  )
}

export function App() {
  return (
    <AuthProvider>
      <NavigatorBridge />
      <Shell />
      <ToastHost />
    </AuthProvider>
  )
}
