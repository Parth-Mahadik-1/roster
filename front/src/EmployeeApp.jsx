import { useEffect, useMemo, useState } from 'react'
import { employeeApi } from './api'

const initialPeriod = { year: 2026, month: 12 }

export default function EmployeeApp({ account, onSignOut }) {
  const [period, setPeriod] = useState(initialPeriod)
  const [dashboard, setDashboard] = useState(null)
  const [view, setView] = useState('schedule')
  const [calendarView, setCalendarView] = useState('month')
  const [weekIndex, setWeekIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState({ request_type: 'Leave', unavailable_from: '', unavailable_to: '' })

  async function loadDashboard(quiet = false) {
    if (quiet) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const data = await employeeApi.getDashboard(period.year, period.month)
      setDashboard(data)
      if (!form.unavailable_from) {
        setForm((current) => ({ ...current, unavailable_from: data.calendar.starts_on, unavailable_to: data.calendar.starts_on }))
      }
    } catch (failure) {
      setError(failure.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    loadDashboard()
    const refreshTimer = window.setInterval(() => loadDashboard(true), 30000)
    return () => window.clearInterval(refreshTimer)
  }, [period.year, period.month])

  function changePeriod(delta) {
    if (calendarView === 'week') {
      const weeksInMonth = Math.ceil(new Date(period.year, period.month, 0).getDate() / 7)
      const nextWeek = weekIndex + delta
      if (nextWeek >= 0 && nextWeek < weeksInMonth) {
        setWeekIndex(nextWeek)
        return
      }
      const monthIndex = period.month - 1 + delta
      const nextPeriod = { year: period.year + Math.floor(monthIndex / 12), month: ((monthIndex % 12) + 12) % 12 + 1 }
      setPeriod(nextPeriod)
      setWeekIndex(delta < 0 ? Math.ceil(new Date(nextPeriod.year, nextPeriod.month, 0).getDate() / 7) - 1 : 0)
      return
    }
    const monthIndex = period.month - 1 + delta
    setPeriod({ year: period.year + Math.floor(monthIndex / 12), month: ((monthIndex % 12) + 12) % 12 + 1 })
  }

  async function submitRequest(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setSubmitting(true)
    try {
      await employeeApi.createRequest(form)
      setNotice('Your request was submitted for manager review.')
      await loadDashboard(true)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setSubmitting(false)
    }
  }

  const visibleSchedule = useMemo(
    () => filterByCalendarView(dashboard?.schedule || [], calendarView, weekIndex),
    [dashboard, calendarView, weekIndex],
  )
  const visibleUpdates = useMemo(
    () => filterByCalendarView(dashboard?.updates || [], calendarView, weekIndex, true),
    [dashboard, calendarView, weekIndex],
  )

  return <div className="employee-shell">
    <aside className="employee-sidebar">
      <div className="brand"><span className="brand-mark">D</span><span>datastraw</span></div>
      <div className="employee-identity"><strong>{account.name}</strong><span>Employee #{account.employee_id}</span></div>
      <nav className="main-nav">
        <button className={`nav-item ${view === 'schedule' ? 'active' : ''}`} onClick={() => setView('schedule')}>▦ <span>My schedule</span></button>
        <button className={`nav-item ${view === 'requests' ? 'active' : ''}`} onClick={() => setView('requests')}>◉ <span>My requests</span></button>
      </nav>
      {view === 'schedule' && <div className="side-section employee-view-switch"><p className="side-title">CALENDAR VIEW</p><div className="view-toggle"><button className={calendarView === 'week' ? 'selected' : ''} onClick={() => { setCalendarView('week'); setWeekIndex(0) }}>Week</button><button className={calendarView === 'month' ? 'selected' : ''} onClick={() => setCalendarView('month')}>Month</button></div></div>}
      <div className="manager-account"><button type="button" onClick={onSignOut}>Sign out</button></div>
    </aside>

    <main className="content">
      <header className="topbar">
        <div><p className="eyebrow">EMPLOYEE / PRIVATE VIEW</p><h1>{view === 'schedule' ? 'My daily schedule' : 'My leave requests'} <span>{dashboard?.calendar.label || 'Loading…'}{calendarView === 'week' && view === 'schedule' ? ` · Week ${weekIndex + 1}` : ''}</span></h1>{dashboard?.roster.updated_at && view === 'schedule' && <p className="roster-version">Manager roster updated {formatDateTime(dashboard.roster.updated_at)}</p>}</div>
        <div className="top-actions"><button className="icon-button" onClick={() => changePeriod(-1)} aria-label="Previous period">←</button><button className="icon-button" onClick={() => changePeriod(1)} aria-label="Next period">→</button><button className="refresh-roster" onClick={() => loadDashboard(true)} disabled={refreshing}>{refreshing ? 'Checking…' : 'Refresh schedule'}</button></div>
      </header>
      {error && <div className="alert error"><strong>Could not load your information.</strong> {error}</div>}
      {notice && <div className="alert success">{notice}</div>}
      {loading ? <div className="loading">Loading your information…</div> : dashboard && (view === 'schedule'
        ? <Schedule dashboard={dashboard} schedule={visibleSchedule} updates={visibleUpdates} calendarView={calendarView} />
        : <Requests dashboard={dashboard} form={form} setForm={setForm} onSubmit={submitRequest} submitting={submitting} />)}
    </main>
  </div>
}

function Schedule({ dashboard, schedule, updates, calendarView }) {
  const [openUpdate, setOpenUpdate] = useState(null)
  const removedUpdates = updates.filter((item) => item.type === 'Removed')
  return <>
    <section className="metrics employee-metrics"><Metric label="Scheduled days" value={dashboard.summary.scheduled_days} tone="blue" /><Metric label="Scheduled hours" value={dashboard.summary.scheduled_hours} tone="purple" /><Metric label="Roster changes" value={dashboard.updates.length} tone="orange" /></section>
    {removedUpdates.length > 0 && <section className="alert warning roster-removals"><strong>Manager removed scheduled work</strong>{removedUpdates.map((change) => <p key={change.previous.id}>Your {formatDate(change.previous.date)} assignment for {change.previous.brand} · {change.previous.process}, {change.previous.shift} {change.previous.starts_at}–{change.previous.ends_at}, was removed in the roster updated {formatDateTime(change.updated_at)}.</p>)}</section>}
    <section className="employee-panel"><div className="panel-heading"><div><span>YOUR ASSIGNMENTS</span><h2>{calendarView === 'week' ? 'Weekly work plan' : 'Monthly work plan'}</h2></div><b>{schedule.length}</b></div>
      {schedule.length ? <div className="employee-schedule-list">{schedule.map((item) => <article className={`employee-shift ${item.change ? 'changed' : ''}`} key={item.id}>
        <div className="employee-shift-main"><time>{formatDate(item.date)}</time><div><strong>{item.brand} · {item.process}</strong><span>{item.shift} · {item.starts_at}–{item.ends_at}</span></div><div className="employee-shift-actions"><em>{item.status}</em>{item.change && <button className="roster-update-button" onClick={() => setOpenUpdate(openUpdate === item.id ? null : item.id)}>Updated</button>}</div></div>
        {item.change && openUpdate === item.id && <UpdateMessage change={item.change} />}
      </article>)}</div> : <div className="empty-history">No assignments are published for you in this {calendarView}.</div>}
    </section>
  </>
}

function UpdateMessage({ change }) {
  const current = change.current
  if (change.type === 'Coverage') {
    return <div className="roster-update-message"><strong>Manager assigned an additional coverage task</strong><p>Keep your original task on {formatFullDate(current.date)} and also cover <b>{current.brand}</b> for <b>{current.process}</b>, during the <b>{current.shift}</b> shift from <b>{current.starts_at} to {current.ends_at}</b>.</p></div>
  }
  return <div className="roster-update-message"><strong>Manager updated your roster</strong><p>Your assignment on {formatFullDate(current.date)} is now <b>{current.brand}</b> for <b>{current.process}</b>, working the <b>{current.shift}</b> shift from <b>{current.starts_at} to {current.ends_at}</b>.</p>{change.previous && <p className="previous-assignment">Previously: {change.previous.brand} · {change.previous.process}, {change.previous.shift} {change.previous.starts_at}–{change.previous.ends_at}.</p>}{change.updated_at && <small>Roster changed {formatDateTime(change.updated_at)}</small>}</div>
}

function Requests({ dashboard, form, setForm, onSubmit, submitting }) {
  const update = (field, value) => setForm({ ...form, [field]: value })
  return <section className="employee-request-grid">
    <form className="request-form" onSubmit={onSubmit}>
      <div className="panel-heading"><div><span>NEW REQUEST</span><h2>Leave or availability</h2></div></div>
      <label>Request type<select value={form.request_type} onChange={(event) => { const type = event.target.value; setForm({ ...form, request_type: type, unavailable_to: type === 'Unavailable' ? form.unavailable_from : form.unavailable_to }) }}><option>Leave</option><option>Comp Off</option><option>Unavailable</option></select></label>
      <div className="date-pair"><label>From<input type="date" value={form.unavailable_from} min={dashboard.calendar.starts_on} max={dashboard.calendar.ends_on} onChange={(event) => { const from = event.target.value; setForm({ ...form, unavailable_from: from, unavailable_to: form.request_type === 'Unavailable' ? from : form.unavailable_to }) }} required /></label><label>To<input type="date" value={form.unavailable_to} min={form.unavailable_from || dashboard.calendar.starts_on} max={dashboard.calendar.ends_on} disabled={form.request_type === 'Unavailable'} onChange={(event) => update('unavailable_to', event.target.value)} required /></label></div>
      <button className="submit-request" disabled={submitting}>{submitting ? 'Submitting…' : 'Submit request'}</button>
    </form>
    <section className="employee-panel"><div className="panel-heading"><div><span>YOUR REQUESTS</span><h2>Status history</h2></div><b>{dashboard.requests.length}</b></div>
      {dashboard.requests.length ? <div className="employee-schedule-list">{dashboard.requests.map((item) => <article className="employee-shift" key={item.id}><div className="employee-shift-main"><time>{formatDate(item.from)}{item.to !== item.from ? ` – ${formatDate(item.to)}` : ''}</time><div><strong>{item.type}</strong><span>{item.review_note || 'No manager note'}</span></div><em className={`request-status ${item.status.toLowerCase()}`}>{item.status}</em></div></article>)}</div> : <div className="empty-history">You have no requests for this month.</div>}
    </section>
  </section>
}

function filterByCalendarView(items, calendarView, weekIndex, changes = false) {
  if (calendarView === 'month') return items
  const firstDay = weekIndex * 7 + 1
  const lastDay = firstDay + 6
  return items.filter((item) => {
    const value = changes ? (item.current || item.previous).date : item.date
    const day = Number(value.slice(-2))
    return day >= firstDay && day <= lastDay
  })
}

function Metric({ label, value, tone }) { return <article className={`metric ${tone}`}><span>{label}</span><strong>{value}</strong></article> }
function formatDate(value) { return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) }
function formatFullDate(value) { return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) }
function formatDateTime(value) { return new Date(value).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) }
