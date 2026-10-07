// Central client for every call to the combined FastAPI/React server.
// The browser talks only to the current host through the /api prefix.

export const API_BASE = import.meta.env.VITE_API_URL || '/api';

const TOKEN_KEY = 'docflow_token';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = 'GET', body, isForm = false, auth = true } = {}) {
  const headers = {};
  if (!isForm) headers['Content-Type'] = 'application/json';
  if (auth) {
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: isForm ? body : body !== undefined ? JSON.stringify(body) : undefined,
  });

  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && auth) {
      setToken(null);
      if (window.location.pathname !== `${import.meta.env.BASE_URL}login`) {
        window.location.replace(`${import.meta.env.BASE_URL}login`);
      }
    }
    const detail = (payload && payload.detail) || response.statusText || 'Request failed';
    throw new ApiError(detail, response.status, payload);
  }

  return payload;
}

// ---------- Auth ----------
export const authApi = {
  login: (email, password) => request('/login', { method: 'POST', body: { email, password }, auth: false }),
  forgotPassword: (email) => request('/auth/forgot-password', { method: 'POST', body: { email }, auth: false }),
  resetPassword: (token, new_password) => request('/auth/reset-password', { method: 'POST', body: { token, new_password }, auth: false }),
  changePassword: (current_password, new_password) => request('/auth/change-password', { method: 'POST', body: { current_password, new_password } }),
  signup: (data) => request('/signup', { method: 'POST', body: data, auth: false }),
  signupOrganization: (data) => request('/organizations/signup', { method: 'POST', body: data, auth: false }),
  verifyEmail: (token) => request(`/auth/verify-email?token=${encodeURIComponent(token)}`, { method: 'POST', auth: false }),
  superAdmin: () => request('/auth/super-admin', { method: 'POST', auth: false }),
  demo: () => request('/auth/demo', { method: 'POST', auth: false }),
  me: () => request('/me'),
  googleStartUrl: () => `${API_BASE}/auth/google/start`,
};

// ---------- Projects ----------
export const projectsApi = {
  list: () => request('/projects'),
  create: (data) => request('/projects', { method: 'POST', body: data }),
  documents: (projectId) => request(`/projects/${encodeURIComponent(projectId)}/documents`),
  activity: (projectId) => request(`/projects/${encodeURIComponent(projectId)}/activity`),
  pendingApprovals: (projectId) => request(`/projects/${encodeURIComponent(projectId)}/pending-approvals`),
};

export const documentsApi = {
  listAll: () => request('/documents'),
  upload: (formData) => request('/upload', { method: 'POST', body: formData, isForm: true }),
  uploadBatch: (formData) => request('/upload/batch', { method: 'POST', body: formData, isForm: true }),
  versions: (documentId) => request(`/documents/${encodeURIComponent(documentId)}/versions`),
  uploadVersion: (documentId, formData) => request(`/documents/${encodeURIComponent(documentId)}/versions`, { method: 'POST', body: formData, isForm: true }),
  openUrl: (documentId) => `${API_BASE}/documents/${encodeURIComponent(documentId)}/open?token=${encodeURIComponent(getToken() || '')}`,
  submit: (documentId) => request(`/documents/${encodeURIComponent(documentId)}/submit`, { method: 'POST' }),
  approve: (documentId, approval_reason) => request(`/documents/${encodeURIComponent(documentId)}/approve`, { method: 'POST', body: { approval_reason } }),
  reject: (documentId, rejection_reason) => request(`/documents/${encodeURIComponent(documentId)}/reject`, { method: 'POST', body: { rejection_reason } }),
  remove: (documentId) => request(`/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' }),
};

// ---------- RAG: ask / search ----------
export const ragApi = {
  ask: (query, project_id) => request('/ask', { method: 'POST', body: { query, project_id } }),
  search: (query, project_id) => request('/search', { method: 'POST', body: { query, project_id } }),
  stages: () => request('/stages'),
};

export const chatApi = {
  sessions: (project_id, mode = 'query') => request(`/chat/sessions?mode=${mode}${project_id ? `&project_id=${encodeURIComponent(project_id)}` : ''}`),
  createSession: (data) => request('/chat/sessions', { method: 'POST', body: data }),
  messages: (sessionId) => request(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
  addMessage: (sessionId, data) => request(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`, { method: 'POST', body: data }),
  removeSession: (sessionId) => request(`/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),
};

// ---------- Agents ----------
export const agentsApi = {
  outline: (project_id, stage) => request('/agents/drafting/outline', { method: 'POST', body: { project_id, stage } }),
  analyzeGaps: (project_id) => request(`/agents/gap-detection/analyze?project_id=${encodeURIComponent(project_id)}`, { method: 'POST' }),
  followups: (query, project_id) => request('/agents/query/followups', { method: 'POST', body: { query, project_id } }),
};

// ---------- Studio: drafting + scanning ----------
export const studioApi = {
  generateDraft: (data) => request('/studio/generate-draft', { method: 'POST', body: data }),
  scanDraft: (data) => request('/studio/scan-draft', { method: 'POST', body: data }),
  scanUpload: (formData) => request('/studio/scan-upload', { method: 'POST', body: formData, isForm: true }),
  scanProjectDocument: (projectId, documentId) => request(`/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}/scan`, { method: 'POST' }),
  saveDraft: (data) => request('/studio/save-draft', { method: 'POST', body: data }),
  savedDrafts: () => request('/studio/saved-drafts'),
  savedDraft: (filename) => request(`/studio/saved-drafts/${encodeURIComponent(filename)}`),
  downloadDraft: (data) => request('/studio/download-draft', { method: 'POST', body: data }),
};

// ---------- Notes ----------
export const notesApi = {
  list: () => request('/notes'),
  create: (data) => request('/notes', { method: 'POST', body: data }),
  personalDocuments: () => request('/notes/documents'),
  uploadPersonalDocument: (formData) => request('/notes/documents', { method: 'POST', body: formData, isForm: true }),
};

// ---------- Admin ----------
export const adminApi = {
  listUsers: () => request('/admin/users'),
  createUser: (data) => request('/admin/users', { method: 'POST', body: data }),
  removeUser: (userId) => request(`/admin/users/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  assignAccess: (data) => request('/admin/access', { method: 'POST', body: data }),
  revokeAccess: (userId) => request(`/admin/access/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  assignProjectAccess: (data) => request('/admin/project-access', { method: 'POST', body: data }),
  removeProjectAccess: (userId, projectId) => request(`/admin/project-access/${encodeURIComponent(userId)}/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  setRole: (userId, projectId, role) => request(`/admin/users/${encodeURIComponent(userId)}/role?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: { role } }),
  auditLog: (limit = 100) => request(`/admin/audit-log?limit=${limit}`),
  rbacMatrix: () => request('/rbac/matrix'),
  simulateAbac: (data) => request('/abac/simulate', { method: 'POST', body: data }),
};

// ---------- Notifications ----------
export const notificationsApi = {
  list: (since) => request(`/notifications${since ? `?since=${encodeURIComponent(since)}` : ''}`),
};

export { ApiError };
