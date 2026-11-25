import { useEffect, useMemo, useState } from 'react'
import { dashboardApi } from './api'

const initialPeriod = { year: 2026, month: 12 }

export default function App({ manager, onSignOut }) {
  const [period, setPeriod] = useState(initialPeriod)
  const [dashboard, setDashboard] = useState(null)
  const [reportData, setReportData] = useState(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ brand: '', pod: '', process: '', shift: '' })
  const [reportFilters, setReportFilters] = useState({ brand: '', process: '', shift: '' })
  const [calendarView, setCalendarView] = useState('month')
  const [weekIndex, setWeekIndex] = useState(0)
  const [activeView, setActiveView] = useState('roster')
  const [requestData, setRequestData] = useState(null)
  const [requestError, setRequestError] = useState('')
  const [requestNotice, setRequestNotice] = useState('')
  const [reviewingRequestId, setReviewingRequestId] = useState(null)
  const [simulatingRequestId, setSimulatingRequestId] = useState(null)
  const [simulation, setSimulation] = useState(null)

  async function loadDashboard(nextPeriod = period) {
    setLoading(true); setError('')
    try { setDashboard(await dashboardApi.getDashboard(nextPeriod.year, nextPeriod.month)) }
    catch (requestError) { setError(requestError.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadDashboard() }, [period.year, period.month])
  useEffect(() => { if (activeView === 'requests') loadRequests() }, [activeView, period.year, period.month])
  useEffect(() => { if (activeView === 'reports') loadReport() }, [activeView, period.year, period.month])

  async function generateRoster() {
    setGenerating(true); setError('')
    try { setDashboard(await dashboardApi.generateDashboard(period.year, period.month)) }
    catch (requestError) { setError(requestError.message) }
    finally { setGenerating(false) }
  }

  async function loadRequests(nextPeriod = period) {
    setRequestError('')
    try { setRequestData(await dashboardApi.getRequests(nextPeriod.year, nextPeriod.month)) }
    catch (requestFailure) { setRequestError(requestFailure.message) }
  }

  async function loadReport(nextPeriod = period) {
    setReportLoading(true); setReportData(null); setError('')
    try { setReportData(await dashboardApi.getReport(nextPeriod.year, nextPeriod.month)) }
    catch (requestFailure) { setError(requestFailure.message) }
    finally { setReportLoading(false) }
  }

  async function reviewRequest(requestId, decision) {
    setRequestError(''); setRequestNotice(''); setReviewingRequestId(requestId)
    try {
      const response = decision === 'Approved'
        ? await dashboardApi.approveLeaveRequest(requestId)
        : await dashboardApi.rejectLeaveRequest(requestId)
      if (response.dashboard) setDashboard(response.dashboard)
      setRequestData(response.requests)
      setSimulation(null)
      setRequestNotice(decision === 'Approved' ? 'Request approved. Only the simulated coverage tasks were added; all other assignments stayed unchanged.' : 'Request rejected. No roster or availability record was changed.')
    } catch (requestFailure) { setRequestError(requestFailure.message) }
    finally { setReviewingRequestId(null) }
  }

  async function simulateReplacement(requestId) {
    setRequestError(''); setRequestNotice(''); setSimulatingRequestId(requestId); setSimulation(null)
    try { setSimulation((await dashboardApi.simulateRequest(requestId)).simulation) }
    catch (requestFailure) { setRequestError(requestFailure.message) }
    finally { setSimulatingRequestId(null) }
  }

  async function cancelApproval(requestId) {
    setRequestError(''); setRequestNotice(''); setReviewingRequestId(requestId)
    try {
      const response = await dashboardApi.cancelLeaveApproval(requestId)
      if (response.dashboard) setDashboard(response.dashboard)
      setRequestData(response.requests)
      setSimulation(null)
      setRequestNotice('Approval cancelled. The employee is available again and targeted coverage was removed.')
    } catch (requestFailure) { setRequestError(requestFailure.message) }
    finally { setReviewingRequestId(null) }
  }

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

  const rows = useMemo(() => filterRoster(dashboard, search, filters), [dashboard, search, filters])
  const allDates = dashboard ? monthDates(dashboard.calendar.year, dashboard.calendar.month) : []
  const dates = calendarView === 'week' ? allDates.slice(weekIndex * 7, weekIndex * 7 + 7) : allDates
  const activeFilterCount = Object.values(filters).filter(Boolean).length + (search ? 1 : 0)

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">D</span><span>datastraw</span></div>
      <nav className="main-nav"><button className={`nav-item ${activeView === 'roster' ? 'active' : ''}`} onClick={() => setActiveView('roster')}>▦ <span>Roster</span></button><button className={`nav-item ${activeView === 'requests' ? 'active' : ''}`} onClick={() => setActiveView('requests')}>◉ <span>Requests</span></button><button className="nav-item">◷ <span>Time off</span></button><button className={`nav-item ${activeView === 'reports' ? 'active' : ''}`} onClick={() => setActiveView('reports')}>▤ <span>Reports</span></button></nav>
      {activeView === 'roster' && <><div className="side-section"><p className="side-title">CALENDAR VIEW</p><div className="view-toggle"><button className={calendarView === 'week' ? 'selected' : ''} onClick={() => { setCalendarView('week'); setWeekIndex(0) }}>Week</button><button className={calendarView === 'month' ? 'selected' : ''} onClick={() => setCalendarView('month')}>Month</button></div></div>
      <div className="side-section"><div className="filter-title-row"><p className="side-title">FILTER ROSTER</p>{activeFilterCount > 0 && <button className="clear-filters" onClick={() => { setSearch(''); setFilters({ brand: '', pod: '', process: '', shift: '' }) }}>Clear</button>}</div><input className="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search employee" />
        <Filter label="Brand" value={filters.brand} onChange={(value) => setFilters({ ...filters, brand: value })} items={dashboard?.filters.brands} itemName="name" />
        <Filter label="Pod" value={filters.pod} onChange={(value) => setFilters({ ...filters, pod: value })} items={dashboard?.filters.pods} itemName="pod_name" />
        <Filter label="Process" value={filters.process} onChange={(value) => setFilters({ ...filters, process: value })} items={dashboard?.filters.processes} itemName="name" />
        <Filter label="Shift" value={filters.shift} onChange={(value) => setFilters({ ...filters, shift: value })} items={dashboard?.filters.shifts} itemName="name" />
      </div></>}
      {activeView === 'reports' && <div className="side-section report-scope"><p className="side-title">REPORT SCOPE</p><strong>{dashboard?.calendar.label || 'Selected month'}</strong><span>All active staffing requirements and live scheduled assignments.</span></div>}
      <div className="manager-account"><span>{manager.name} · Manager</span><button type="button" onClick={onSignOut}>Sign out</button></div>
      <div className="side-footer"><span className="online-dot" />Live Supabase data</div>
    </aside>

    <main className="content">
      <header className="topbar"><div><p className="eyebrow">{activeView === 'requests' ? 'PEOPLE / AVAILABILITY' : activeView === 'reports' ? 'OPERATIONS / INSIGHTS' : 'OPERATIONS / ROSTER'}</p><h1>{activeView === 'requests' ? 'Leave & availability' : activeView === 'reports' ? 'Staffing report' : 'Schedule calendar'} <span>{dashboard?.calendar.label || 'Loading…'}</span></h1></div><div className="top-actions"><button className="icon-button" onClick={() => changePeriod(-1)} aria-label="Previous month">←</button><button className="icon-button" onClick={() => changePeriod(1)} aria-label="Next month">→</button>{activeView === 'roster' && <button className="generate" onClick={generateRoster} disabled={generating}>{generating ? 'Generating…' : 'Generate roster'}</button>}</div></header>

      {error && <div className="alert error"><strong>API unavailable.</strong> {error}</div>}
      {activeView === 'requests' ? <RequestsPage requestData={requestData} error={requestError} notice={requestNotice} onReview={reviewRequest} onCancel={cancelApproval} reviewingRequestId={reviewingRequestId} onSimulate={simulateReplacement} simulatingRequestId={simulatingRequestId} simulation={simulation} /> : activeView === 'reports' ? <ReportsPage dashboard={dashboard} reportData={reportData} loading={reportLoading} filters={reportFilters} setFilters={setReportFilters} /> : dashboard && <><section className="metrics"><Metric label="Employees" value={dashboard.summary.employees} tone="blue" /><Metric label="Scheduled shifts" value={dashboard.summary.scheduled_assignments} tone="purple" /><Metric label="Coverage" value={dashboard.summary.coverage_status === 'feasible' ? 'Covered' : 'Attention'} tone={dashboard.summary.coverage_status === 'feasible' ? 'green' : 'amber'} /><Metric label="Manager actions" value={dashboard.summary.manager_action_count} tone="orange" /></section>
        <section className="toolbar"><div><strong>{calendarView === 'week' ? `Week ${weekIndex + 1} roster` : 'Monthly roster'}</strong><span>{rows.length} people shown · {dashboard.summary.scheduled_assignments} assignments{activeFilterCount ? ` · ${activeFilterCount} filter${activeFilterCount > 1 ? 's' : ''} active` : ''}</span></div><div className="legend"><i className="cs" />Support<i className="acc" />Accounts<i className="om" />Order<i className="ret" />Retention<i className="ndr" />NDR</div></section>
        {dashboard.roster.manager_messages.length > 0 && <section className="alert warning"><strong>Manager actions needed</strong>{dashboard.roster.manager_messages.map((message) => <p key={message}>{message}</p>)}</section>}
        {loading ? <div className="loading">Loading live roster…</div> : <RosterGrid rows={rows} dates={dates} filters={filters} />}
      </>}
    </main>
  </div>
}

function RequestsPage({ requestData, error, notice, onReview, onCancel, reviewingRequestId, onSimulate, simulatingRequestId, simulation }) {
  const requests = requestData?.requests || []
  const pending = requestData?.pending_requests || []
  const approved = requestData?.approved_unavailability || []
  const counts = requestData?.status_counts || {}
  return <section className="requests-page">
    <div className="request-context"><div><strong>Manager leave desk</strong><p>Review employee requests, simulate targeted replacements, then approve without moving unaffected roster assignments.</p></div><span className="coverage-rule">Up to 31 days</span></div>
    {error && <div className="alert error"><strong>Requests could not be loaded.</strong> {error}</div>}
    {notice && <div className="alert success"><strong>Roster updated.</strong> {notice}</div>}
    <section className="request-status-strip"><Metric label="Pending" value={counts.Pending || 0} tone="orange" /><Metric label="Approved" value={counts.Approved || 0} tone="green" /><Metric label="Rejected" value={counts.Rejected || 0} tone="blue" /></section>
    <div className="manager-request-grid">
      <section className="availability-panel"><div className="panel-heading"><div><span>TODAY / ACTION NEEDED</span><h2>Pending requests</h2></div><b>{pending.length}</b></div>{pending.length ? <div className="today-list">{pending.map((item) => <article className="today-item pending-card" key={item.id}><div className="request-avatar">{initials(item.employee)}</div><div><strong>{item.employee} · #{item.employee_id}</strong><span>{item.type} · {formatRequestDate(item.from)} — {formatRequestDate(item.to)}</span></div><button className="simulate-button" onClick={() => onSimulate(item.id)} disabled={simulatingRequestId === item.id}>{simulatingRequestId === item.id ? 'Checking…' : 'Run simulator'}</button></article>)}</div> : <div className="empty-status"><span>✓</span><strong>No pending requests</strong><p>New employee requests will appear here.</p></div>}</section>
      <section className="availability-panel"><div className="panel-heading"><div><span>APPROVED STATUS</span><h2>Approved unavailability</h2></div><b>{approved.length}</b></div>{approved.length ? <div className="today-list">{approved.map((item) => <article className="today-item approved-unavailability" key={item.id}><div className="request-avatar">{initials(item.employee)}</div><div className="approved-request-copy"><strong>{item.employee} · #{item.employee_id}</strong><span>Unavailable {formatRequestDate(item.from)} — {formatRequestDate(item.to)}</span><small><b>Replacement status:</b> {item.coverage_summary}</small></div><div className="approved-actions"><em className="approved">Approved</em><button type="button" onClick={() => onCancel(item.id)} disabled={reviewingRequestId === item.id}>{reviewingRequestId === item.id ? 'Cancelling…' : 'Cancel approval'}</button></div></article>)}</div> : <div className="empty-status"><span>✓</span><strong>No approved unavailability</strong><p>Approved employee ID, name, dates, and replacement assignments will appear here.</p></div>}</section>
    </div>
    {simulation && <ReplacementSimulator simulation={simulation} onApprove={() => onReview(simulation.request_id, 'Approved')} approving={reviewingRequestId === simulation.request_id} />}
    <section className="request-history"><div className="panel-heading"><div><span>STATUS PANEL</span><h2>{requestData?.calendar.label || 'Selected month'} request history</h2></div><b>{requests.length}</b></div>{requests.length ? <div className="request-table">{requests.map((item) => <article className="request-row" key={item.id}><div><strong>{item.employee}</strong><span>#{item.employee_id} · {item.designation} · {item.pod}</span></div><div><small>TYPE</small><span>{item.type}</span></div><div><small>DATES</small><span>{formatRequestDate(item.from)} — {formatRequestDate(item.to)}</span></div><div><small>STATUS</small><em className={`request-status ${item.status.toLowerCase()}`}>{item.status}</em></div>{item.status === 'Pending' && <div className="review-actions"><button onClick={() => onSimulate(item.id)} disabled={simulatingRequestId === item.id}>Simulate</button><button onClick={() => onReview(item.id, 'Rejected')} disabled={reviewingRequestId === item.id}>Reject</button></div>}</article>)}</div> : <div className="empty-history">No employee requests exist for this month.</div>}</section>
  </section>
}

function ReplacementSimulator({ simulation, onApprove, approving }) {
  return <section className={`replacement-simulator ${simulation.feasible ? 'feasible' : 'blocked'}`}><div className="panel-heading"><div><span>REPLACEMENT SIMULATOR</span><h2>{simulation.employee} · #{simulation.employee_id}</h2></div><b>{simulation.affected_task_count}</b></div><p className="simulator-summary">{simulation.explanation}</p>{simulation.replacements.length > 0 && <div className="replacement-list">{simulation.replacements.map((item) => <article key={`${item.date}-${item.brand_process_id}-${item.shift_id}`}><div><strong>{formatRequestDate(item.date)} · {item.brand} · {item.process}</strong><span>{item.shift}</span></div><div><small>RECOMMENDED COVER</small><strong>{item.recommended.employee} · #{item.recommended.employee_id}</strong><span>{item.recommended.already_working ? `Present with ${item.recommended.current_task_count} own task` : 'Eligible and available'}</span></div></article>)}</div>}{simulation.gaps.length > 0 && <div className="simulator-gaps">{simulation.gaps.map((gap) => <p key={`${gap.date}-${gap.brand}-${gap.shift}`}>{formatRequestDate(gap.date)} · {gap.brand} · {gap.process}: {gap.message}</p>)}</div>}<div className="simulator-footer"><span>{simulation.unaffected_task_count} unaffected assignments will stay exactly as scheduled.</span><button onClick={onApprove} disabled={!simulation.feasible || approving}>{approving ? 'Approving…' : 'Approve targeted coverage'}</button></div></section>
}

function ReportsPage({ dashboard, reportData, loading, filters, setFilters }) {
  if (loading || !reportData?.report) return <div className="loading">Loading live staffing analytics…</div>
  const report = reportData.report
  const liveAssignments = (reportData.roster.assignments || []).filter((assignment) => matchesReportFilter(assignment, filters))
  const lanes = (report.lanes || []).filter((lane) => matchesReportFilter(lane, filters))
  const brands = aggregateBrandLanes(lanes)
  const dayShiftAssignments = liveAssignments.filter((assignment) => !normalise(assignment.shift).startsWith('NIGHT')).length
  const workingEmployees = new Set(liveAssignments.map((assignment) => String(assignment.employee_id))).size
  const summary = {
    required_assignments: lanes.reduce((total, lane) => total + lane.required, 0),
    assigned_assignments: liveAssignments.length,
    working_employees: workingEmployees,
    day_shift_assignments: dayShiftAssignments,
    night_shift_assignments: liveAssignments.length - dayShiftAssignments,
    average_daily_staff: Math.round((liveAssignments.length / new Date(dashboard.calendar.year, dashboard.calendar.month, 0).getDate()) * 10) / 10,
  }
  const requiredPeak = Math.max(...brands.flatMap((item) => [item.required, item.assigned]), 1)
  const pods = podWorkforce({ ...dashboard, roster: reportData.roster }, filters)
  const totalShiftTasks = summary.day_shift_assignments + summary.night_shift_assignments
  const dayShare = totalShiftTasks ? Math.round((summary.day_shift_assignments / totalShiftTasks) * 100) : 0

  return <section className="reports-page">
    <section className="report-intro"><div><strong>Monthly staffing analytics</strong><p>Required staffing includes active dates, Sunday rules, and applicable public holidays. Assigned tasks are read from the live generated roster.</p></div><span className={reportData.coverage_status === 'feasible' ? 'report-status covered' : 'report-status attention'}>{reportData.coverage_status === 'feasible' ? 'Coverage validated' : 'Coverage needs attention'}</span></section>

    <section className="report-metrics">
      <ReportMetric label="Working employees" value={summary.working_employees} note="unique people scheduled" tone="blue" />
      <ReportMetric label="Required staff shifts" value={summary.required_assignments} note="planned monthly coverage" tone="purple" />
      <ReportMetric label="Assigned tasks" value={summary.assigned_assignments} note="live roster allocation" tone="green" />
      <ReportMetric label="Average daily staff" value={summary.average_daily_staff} note="employees working per day" tone="orange" />
    </section>

    <section className="report-filters"><div><span>REPORT FILTERS</span><strong>Refine the analytics</strong></div><label>Brand<select value={filters.brand} onChange={(event) => setFilters({ ...filters, brand: event.target.value })}><option value="">All brands</option>{(dashboard.filters.brands || []).map((item) => <option value={item.name} key={item.id}>{item.name}</option>)}</select></label><label>Process<select value={filters.process} onChange={(event) => setFilters({ ...filters, process: event.target.value })}><option value="">All processes</option>{(dashboard.filters.processes || []).map((item) => <option value={item.name} key={item.id}>{item.name}</option>)}</select></label><label>Shift<select value={filters.shift} onChange={(event) => setFilters({ ...filters, shift: event.target.value })}><option value="">All shifts</option>{(dashboard.filters.shifts || []).map((item) => <option value={item.name} key={item.id}>{item.name}</option>)}</select></label>{Object.values(filters).some(Boolean) && <button type="button" onClick={() => setFilters({ brand: '', process: '', shift: '' })}>Clear</button>}</section>

    <section className="report-grid">
      <article className="report-panel brand-chart"><div className="report-heading"><div><span>BRAND STAFFING</span><h2>Required versus assigned</h2></div><p>Monthly staff-shifts</p></div><div className="chart-legend"><span><i className="required-swatch" />Required</span><span><i className="assigned-swatch" />Assigned</span></div><div className="standard-bar-chart" role="img" aria-label="Grouped bar chart comparing required and assigned staff-shifts by brand">{brands.map((item) => <div className="standard-bar-group" key={item.brand} data-tooltip={`${item.brand}: ${item.required} required, ${item.assigned} assigned`}><div className="standard-bar-values"><strong>{item.assigned}</strong><span>/ {item.required}</span></div><div className="standard-bar-columns"><i className="required-column" style={{ height: `${Math.max((item.required / requiredPeak) * 100, 3)}%` }} /><i className="assigned-column" style={{ height: `${Math.max((item.assigned / requiredPeak) * 100, 3)}%` }} /></div><strong className="standard-bar-label">{item.brand}</strong><small>{item.daily_required}/day</small></div>)}</div></article>

      <article className="report-panel shift-chart"><div className="report-heading"><div><span>SHIFT MIX</span><h2>Day and night allocation</h2></div></div><div className="shift-chart-content"><div className="shift-pie" style={{ '--day-share': `${dayShare}%` }} role="img" aria-label={`${dayShare}% day-shift and ${100 - dayShare}% night-shift assignments`} /><div className="shift-breakdown"><div><i className="day-swatch" /><span>Day-shift staff</span><strong>{summary.day_shift_assignments}</strong></div><div><i className="night-swatch" /><span>Night-shift staff</span><strong>{summary.night_shift_assignments}</strong></div><p>{dayShare}% day shift · {100 - dayShare}% night shift</p></div></div></article>
    </section>

    <section className="report-grid lower-report-grid">
      <article className="report-panel pod-workforce-panel"><div className="report-heading"><div><span>POD WORKFORCE</span><h2>Pod-wise employee allocation</h2></div><p>Selected month</p></div><div className="pod-workforce-list">{pods.map((pod) => <article className="pod-workforce-row" key={pod.name}><div className="pod-mark">{initials(pod.name)}</div><div className="pod-details"><strong>{pod.name}</strong><span>{pod.leader ? `Lead: ${pod.leader}` : 'No pod leader recorded'}</span></div><div><small>EMPLOYEES</small><strong>{pod.employees}</strong></div><div><small>SCHEDULED</small><strong>{pod.scheduled}</strong></div><div><small>TASKS</small><strong>{pod.tasks}</strong></div></article>)}</div></article>

      <article className="report-panel report-table-panel"><div className="report-heading"><div><span>COVERAGE REGISTER</span><h2>Brand-level staffing result</h2></div></div><div className="report-table-wrap"><table><thead><tr><th>Brand</th><th>Required / day</th><th>Assigned</th><th>Coverage</th></tr></thead><tbody>{brands.map((item) => <tr key={item.brand}><td>{item.brand}</td><td>{item.daily_required}</td><td>{item.assigned} / {item.required}</td><td><span className={item.coverage_percent >= 100 ? 'coverage-chip good' : 'coverage-chip gap'}>{item.coverage_percent}%</span></td></tr>)}</tbody></table></div></article>
    </section>
  </section>
}

function ReportMetric({ label, value, note, tone }) { return <article className={`report-metric ${tone}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></article> }
function Metric({ label, value, tone }) { return <article className={`metric ${tone}`}><span>{label}</span><strong>{value}</strong></article> }

function matchesReportFilter(item, filters) {
  return (!filters.brand || normalise(item.brand) === normalise(filters.brand))
    && (!filters.process || processCode(item.process) === processCode(filters.process))
    && (!filters.shift || normalise(item.shift).startsWith(normalise(filters.shift)))
}

function aggregateBrandLanes(lanes) {
  const brands = new Map()
  for (const lane of lanes) {
    const item = brands.get(lane.brand) || { brand: lane.brand, required: 0, assigned: 0, daily_required: 0 }
    item.required += lane.required
    item.assigned += lane.assigned
    item.daily_required += lane.daily_required
    brands.set(lane.brand, item)
  }
  return [...brands.values()].map((item) => ({ ...item, daily_required: Math.round(item.daily_required * 10) / 10, coverage_percent: item.required ? Math.round((item.assigned / item.required) * 100) : 0 })).sort((a, b) => b.required - a.required || a.brand.localeCompare(b.brand))
}

function podWorkforce(dashboard, filters = {}) {
  const employees = dashboard?.catalog?.employees || []
  const staffById = new Map(employees.map((employee) => [String(employee.id), employee]))
  const pods = new Map()
  for (const employee of employees) {
    const name = employee.pod || 'Unassigned'
    if (!pods.has(name)) pods.set(name, { name, employees: 0, scheduledIds: new Set(), tasks: 0, leader: '' })
    pods.get(name).employees += 1
  }
  for (const pod of dashboard?.catalog?.pods || []) {
    const entry = pods.get(pod.pod_name)
    const lead = staffById.get(String(pod.pod_lead_id))
    if (entry && lead) entry.leader = lead.name
  }
  for (const assignment of dashboard?.roster?.assignments || []) {
    if (!matchesReportFilter(assignment, filters)) continue
    const employee = staffById.get(String(assignment.employee_id))
    const entry = pods.get(employee?.pod || 'Unassigned')
    if (entry) { entry.scheduledIds.add(String(assignment.employee_id)); entry.tasks += 1 }
  }
  return [...pods.values()].map((pod) => ({ ...pod, scheduled: pod.scheduledIds.size })).sort((a, b) => b.employees - a.employees || a.name.localeCompare(b.name))
}

function Filter({ label, value, onChange, items = [], itemName }) {
  const plural = label === 'Process' ? 'Processes' : `${label}s`
  return <label className="filter"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}><option value="">All {plural}</option>{items.map((item) => <option key={item.id ?? item.pod_id} value={item[itemName]}>{item[itemName]}</option>)}</select></label>
}

function RosterGrid({ rows, dates, filters }) {
  const [staffWidth, setStaffWidth] = useState(300)

  function beginResize(event) {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = staffWidth
    const resize = (moveEvent) => setStaffWidth(Math.max(230, Math.min(460, startWidth + moveEvent.clientX - startX)))
    const stopResize = () => {
      window.removeEventListener('pointermove', resize)
      window.removeEventListener('pointerup', stopResize)
    }
    window.addEventListener('pointermove', resize)
    window.addEventListener('pointerup', stopResize)
  }

  return <section className="roster-wrap"><div className="roster-grid" style={{ gridTemplateColumns: `${staffWidth}px repeat(${dates.length}, minmax(112px, 1fr))` }}><div className="staff-head">EMPLOYEE <span>TITLE, SKILLS & POD</span><button className="column-resizer" type="button" onPointerDown={beginResize} aria-label="Resize employee column" title="Drag to resize employee column" /></div>{dates.map((day) => <div className="day-head" key={day.key}><b>{day.weekday}</b><span>{day.day}</span></div>)}
    {rows.map((person) => <RosterRow key={person.id} person={person} dates={dates} filters={filters} />)}
  </div></section>
}

function RosterRow({ person, dates, filters }) {
  const assignedProcesses = [...new Set([...person.assignments.values()].flat().map((assignment) => assignment.process))]
  const roles = person.eligible_processes?.length ? person.eligible_processes : assignedProcesses.length ? assignedProcesses : [roleCode(person.designation)]
  return <>{<div className="staff-cell"><div className="avatar">{initials(person.name)}</div><div className="staff-details"><strong>{person.name}</strong><span className="designation-label">{person.designation || 'Employee'}</span><div className="staff-meta"><div className="role-badges" aria-label="Eligible processes">{roles.map((role) => <span className={`role-badge ${processClass(role)}`} key={role}>{role}</span>)}</div><span className="pod-label" title={person.pod || 'Unassigned'}>{person.pod || 'Unassigned'}</span></div></div></div>}{dates.map((day) => { const assignments = (person.assignments.get(day.iso) || []).filter((assignment) => matchesAssignment(assignment, filters)); return <div className="shift-cell" key={`${person.id}-${day.iso}`}>{assignments.length > 0 && <div className="shift-stack">{assignments.map((assignment) => <div className={`shift-card ${processClass(assignment.process)}`} key={assignment.role_id}><b>{shortShift(assignment.shift)}{assignment.assignment_source === 'Coverage' ? ' · COVER' : ''}</b><span>{assignment.brand}</span><small>{assignment.process}</small></div>)}</div>}</div> })}</>
}

function filterRoster(dashboard, search, filters) {
  if (!dashboard) return []
  const staff = new Map(dashboard.catalog.employees.map((employee) => [String(employee.id), { ...employee, assignments: new Map() }]))
  for (const assignment of dashboard.roster.assignments) {
    const employee = staff.get(String(assignment.employee_id))
    if (employee) {
      const dailyAssignments = employee.assignments.get(assignment.date) || []
      dailyAssignments.push(assignment)
      employee.assignments.set(assignment.date, dailyAssignments)
    }
  }
  return [...staff.values()].filter((employee) => {
    const values = [...employee.assignments.values()].flat()
    return `${employee.name} ${employee.id}`.toLowerCase().includes(search.toLowerCase())
      && (!filters.pod || employee.pod === filters.pod)
      && values.some((row) => matchesAssignment(row, filters))
  })
}

function matchesAssignment(assignment, filters) {
  return (!filters.brand || normalise(assignment.brand) === normalise(filters.brand))
    && (!filters.process || processCode(assignment.process) === processCode(filters.process))
    && (!filters.shift || normalise(assignment.shift).startsWith(normalise(filters.shift)))
}

function normalise(value = '') { return String(value).trim().toUpperCase() }
function processCode(value = '') {
  const process = normalise(value)
  return ({ 'CUSTOMER SUPPORT': 'CS', 'ABANDONED CART CONVERSION': 'ACC', 'ORDER MANAGEMENT': 'OM', 'CUSTOMER RETENTION': 'RET', 'NON-DELIVERY REPORT MANAGEMENT': 'NDR' })[process] || process
}

function monthDates(year, month) { const format = new Intl.DateTimeFormat('en-US', { weekday: 'short' }); return Array.from({ length: new Date(year, month, 0).getDate() }, (_, index) => { const date = new Date(year, month - 1, index + 1); return { day: index + 1, weekday: format.format(date), iso: `${year}-${String(month).padStart(2, '0')}-${String(index + 1).padStart(2, '0')}`, key: `${year}-${month}-${index + 1}` } }) }
function formatRequestDate(value) { return value ? new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(`${value}T00:00:00`)) : 'Loading…' }
function initials(name = '') { return name.split(' ').map((part) => part[0]).join('').slice(0, 2).toUpperCase() }
function shortShift(shift = '') { return shift.replace(/\s*\(.*/, '') }
function processClass(process = '') { return ({ CS: 'cs', ACC: 'acc', OM: 'om', RET: 'ret', NDR: 'ndr' })[process.toUpperCase()] || 'default' }
function roleCode(designation = '') { const value = designation.toLowerCase(); if (value.includes('customer support') || value.includes('support')) return 'CS'; if (value.includes('account') || value.includes('acc')) return 'ACC'; if (value.includes('order') || value.includes('operation')) return 'OM'; if (value.includes('retention')) return 'RET'; if (value.includes('ndr')) return 'NDR'; return 'EMP' }
