const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

async function request(path, options = {}) {
  const sessionToken = window.sessionStorage.getItem('roster_session_token')
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.method === 'POST' ? { 'X-Roster-Request': '1' } : {}),
      ...(sessionToken ? { 'X-Roster-Session': sessionToken } : {}),
      ...options.headers,
    },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail.join(' ') : payload.detail
    const error = new Error(detail || `The roster API returned ${response.status}.`)
    error.status = response.status
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new Event('roster-sign-in-required'))
    }
    throw error
  }
  return payload
}

export const authApi = {
  session: () => request('/api/auth/session'),
  registerManager: (registration) => request('/api/auth/manager/register', {
    method: 'POST', body: JSON.stringify(registration),
  }),
  login: (credentials) => request('/api/auth/manager/login', {
    method: 'POST', body: JSON.stringify(credentials),
  }),
  loginEmployee: (credentials) => request('/api/auth/employee/login', {
    method: 'POST', body: JSON.stringify(credentials),
  }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
}

export const employeeApi = {
  getDashboard: (year, month) => request(`/api/v1/employee/dashboard?year=${year}&month=${month}`),
  createRequest: (payload) => request('/api/v1/employee/requests', {
    method: 'POST', body: JSON.stringify(payload),
  }),
}

export const dashboardApi = {
  getDashboard: (year, month) => request(`/api/v1/dashboard?year=${year}&month=${month}`),
  getReport: (year, month) => request(`/api/v1/reports?year=${year}&month=${month}`),
  getRequests: (year, month) => request(`/api/v1/requests?year=${year}&month=${month}`),
  simulateRequest: (requestId) => request(`/api/v1/requests/${requestId}/simulate`, { method: 'POST' }),
  generateDashboard: (year, month) => request('/api/v1/dashboard/generate', {
    method: 'POST', body: JSON.stringify({ year, month }),
  }),
  createLeaveRequest: (payload) => request('/api/v1/requests', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  approveLeaveRequest: (requestId, review_note = '') => request(`/api/v1/requests/${requestId}/approve`, {
    method: 'POST', body: JSON.stringify({ review_note }),
  }),
  rejectLeaveRequest: (requestId, review_note = '') => request(`/api/v1/requests/${requestId}/reject`, {
    method: 'POST', body: JSON.stringify({ review_note }),
  }),
  cancelLeaveApproval: (requestId) => request(`/api/v1/requests/${requestId}/cancel`, {
    method: 'POST',
  }),
}
