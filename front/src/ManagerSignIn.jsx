import { useEffect, useState } from 'react'
import App from './App.jsx'
import EmployeeApp from './EmployeeApp.jsx'
import { authApi } from './api'

const emptyCredentials = { employee_id: '', name: '', email: '', password: '' }

export default function ManagerSignIn() {
  const [account, setAccount] = useState(null)
  const [mode, setMode] = useState('manager-login')
  const [credentials, setCredentials] = useState(emptyCredentials)
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    const requireSignIn = () => {
      window.sessionStorage.removeItem('roster_session_token')
      window.sessionStorage.removeItem('roster_role')
      setAccount(null)
      setError('Your session ended. Sign in again.')
    }
    window.addEventListener('roster-sign-in-required', requireSignIn)
    if (!window.sessionStorage.getItem('roster_session_token')) {
      setLoading(false)
      return () => window.removeEventListener('roster-sign-in-required', requireSignIn)
    }
    authApi.session()
      .then((profile) => { if (active) setAccount(profile) })
      .catch((failure) => { if (active && failure.status !== 401) setError(failure.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => {
      active = false
      window.removeEventListener('roster-sign-in-required', requireSignIn)
    }
  }, [])

  function update(field, value) {
    setCredentials({ ...credentials, [field]: value })
  }

  function switchMode(nextMode) {
    setMode(nextMode)
    setCredentials(emptyCredentials)
    setConfirmPassword('')
    setError('')
    setNotice('')
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    if (mode === 'manager-register' && credentials.password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }
    setPending(true)
    try {
      if (mode === 'employee-login') {
        const profile = await authApi.loginEmployee({ name: credentials.name, email: credentials.email })
        rememberSession(profile)
        setAccount(profile)
      } else {
        const payload = { employee_id: Number(credentials.employee_id), email: credentials.email, password: credentials.password }
        if (mode === 'manager-register') {
          const response = await authApi.registerManager(payload)
          setNotice(response.message)
          setMode('manager-login')
          setConfirmPassword('')
        } else {
          const profile = await authApi.login(payload)
          rememberSession(profile)
          setAccount(profile)
        }
      }
    } catch (failure) {
      setError(failure.message)
    } finally {
      setPending(false)
    }
  }

  async function signOut() {
    setError('')
    try {
      await authApi.logout()
      window.sessionStorage.removeItem('roster_session_token')
      window.sessionStorage.removeItem('roster_role')
      setAccount(null)
      setMode('employee-login')
    } catch (failure) {
      setError(failure.message)
    }
  }

  if (loading) return <main className="loading" role="status">Checking session…</main>
  if (account?.role === 'manager') return <App manager={account} onSignOut={signOut} />
  if (account?.role === 'employee') return <EmployeeApp account={account} onSignOut={signOut} />

  const employeeMode = mode === 'employee-login'
  const registrationMode = mode === 'manager-register'
  return <main className="manager-sign-in">
    <form className="request-form" onSubmit={submit}>
      <div className="brand"><span className="brand-mark">D</span><span>datastraw</span></div>
      <div><h1>{employeeMode ? 'Employee sign-in' : registrationMode ? 'Manager registration' : 'Manager sign-in'}</h1><p className="form-note">{employeeMode ? 'Enter the name and email exactly as recorded in the Employee table.' : registrationMode ? 'Your employee ID and company email must match an active Manager record.' : 'Use the manager account verified through Supabase email.'}</p></div>
      {employeeMode
        ? <label>Employee name<input value={credentials.name} onChange={(event) => update('name', event.target.value)} autoComplete="name" required /></label>
        : <label>Employee ID<input value={credentials.employee_id} onChange={(event) => update('employee_id', event.target.value)} inputMode="numeric" pattern="[0-9]+" autoComplete="username" required /></label>}
      <label>Company email<input value={credentials.email} onChange={(event) => update('email', event.target.value)} type="email" autoComplete="email" required /></label>
      {!employeeMode && <label>Password<input value={credentials.password} onChange={(event) => update('password', event.target.value)} type="password" autoComplete={registrationMode ? 'new-password' : 'current-password'} minLength="12" maxLength="256" required /></label>}
      {registrationMode && <label>Confirm password<input value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} type="password" autoComplete="new-password" minLength="12" maxLength="256" required /></label>}
      {error && <p className="alert error" role="alert">{error}</p>}
      {notice && <p className="alert success" role="status">{notice}</p>}
      <button className="submit-request" disabled={pending}>{pending ? 'Signing in…' : registrationMode ? 'Register and verify email' : 'Sign in'}</button>
      <div className="auth-options">
        {!employeeMode && <button className="auth-mode" type="button" onClick={() => switchMode('employee-login')}>Employee sign-in</button>}
        {employeeMode && <button className="auth-mode" type="button" onClick={() => switchMode('manager-login')}>Manager sign-in</button>}
        {mode === 'manager-login' && <button className="auth-mode" type="button" onClick={() => switchMode('manager-register')}>First manager? Register here</button>}
        {registrationMode && <button className="auth-mode" type="button" onClick={() => switchMode('manager-login')}>Already verified? Sign in</button>}
      </div>
    </form>
  </main>
}

function rememberSession(profile) {
  if (profile.session_token) window.sessionStorage.setItem('roster_session_token', profile.session_token)
  if (profile.role) window.sessionStorage.setItem('roster_role', profile.role)
}
