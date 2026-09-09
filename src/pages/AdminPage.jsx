import React, { useEffect, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ShieldCheck, Users, ClipboardList, ScrollText, FlaskConical, Check, X, Clock3, Trash2, Search } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { adminApi, projectsApi, documentsApi } from '../lib/api';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import Dropdown from '../components/ui/Dropdown';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import Modal from '../components/ui/Modal';

const TABS = [
  { id: 'users', label: 'Users & Access', icon: Users },
  { id: 'approvals', label: 'Pending Approvals', icon: ClipboardList },
  { id: 'audit', label: 'Audit Log', icon: ScrollText },
  { id: 'activity', label: 'Project Activity', icon: Clock3 },
  { id: 'abac', label: 'RBAC / ABAC', icon: FlaskConical },
];

const TEAM_OPTIONS = [
  { label: 'Engineering', value: 'Engineering' },
  { label: 'Design', value: 'Design' },
  { label: 'Product', value: 'Product' },
  { label: 'QA', value: 'QA' },
];

const AdminPage = () => {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const requestedProjectId = searchParams.get('project_id') || '';
  const isOrgAdmin = Boolean(user?.is_org_admin);
  const hasProjectAdmin = Object.values(user?.project_roles || {}).some((role) => role === 'admin');
  const hasApprovalAccess = isOrgAdmin || hasProjectAdmin;
  const availableTabs = TABS.filter((tab) => {
    if (tab.id === 'audit' || tab.id === 'activity') return isOrgAdmin;
    if (tab.id === 'approvals') return hasApprovalAccess;
    return true;
  });
  const [tab, setTab] = useState('users');
  const [projects, setProjects] = useState([]);
  const visibleProjects = isOrgAdmin
    ? projects
    : projects.filter((project) => ['admin', 'team_lead'].includes(user?.project_roles?.[project.project_id]));

  useEffect(() => {
    projectsApi.list().then(setProjects).catch(() => {});
  }, []);

  useEffect(() => {
    if (!availableTabs.some((item) => item.id === tab)) setTab(availableTabs[0]?.id || 'users');
  }, [tab, availableTabs]);

  const canManageProjects = isOrgAdmin || Object.values(user?.project_roles || {}).some((role) => ['admin', 'team_lead'].includes(role));
  if (!canManageProjects) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="flex-1 p-8 max-w-5xl mx-auto w-full">
      <div className="mb-6">
        <Link to="/" className="text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1 text-sm mb-4">
          <ArrowLeft size={16} />
          All Projects
        </Link>
        <h1 className="text-2xl font-bold text-gray-100 flex items-center gap-2">
          <ShieldCheck className="text-primary" size={22} />
          Admin
        </h1>
        <p className="text-gray-400 mt-1">User access, approvals, and access-control diagnostics.</p>
      </div>

      <div className="flex gap-1 border-b border-border mb-6 overflow-x-auto">
        {availableTabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap
              ${tab === t.id ? 'border-primary text-primary-light' : 'border-transparent text-gray-400 hover:text-gray-200'}`}
          >
            <t.icon size={16} />
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'users' && <UsersTab projects={visibleProjects} currentUser={user} initialProjectId={requestedProjectId} />}
      {tab === 'approvals' && <ApprovalsTab projects={visibleProjects} />}
      {tab === 'audit' && isOrgAdmin && <AuditTab />}
      {tab === 'activity' && isOrgAdmin && <ProjectActivityTab projects={visibleProjects} />}
      {tab === 'abac' && <AbacTab projects={visibleProjects} />}
    </div>
  );
};

const ProjectActivityTab = ({ projects }) => {
  const [projectId, setProjectId] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async (value) => {
    setProjectId(value);
    setData(null);
    if (!value) return;
    setLoading(true);
    setError('');
    try {
      setData(await projectsApi.activity(value));
    } catch (err) {
      setError(err.message || 'Could not load project activity.');
    } finally {
      setLoading(false);
    }
  };

  const projectOptions = projects.map((project) => ({ label: project.project_name, value: project.project_id }));

  return (
    <div className="space-y-6">
      <Card title="Project activity and ownership" description="See who changed important project data, when it happened, and which stage or document was affected.">
        <Dropdown label="Project" options={projectOptions} value={projectId} onChange={load} placeholder="Select project" />
      </Card>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {loading && <p className="text-sm text-gray-400">Loading project activity…</p>}
      {data && (
        <>
          <Card title={data.project.project_name} description={`${data.documents.length} document(s) currently recorded in this project.`}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div><p className="text-xs text-gray-500">Project ID</p><p className="text-sm text-gray-200 mt-1">{data.project.project_id}</p></div>
              <div><p className="text-xs text-gray-500">Documents</p><p className="text-sm text-gray-200 mt-1">{data.documents.length}</p></div>
              <div><p className="text-xs text-gray-500">Stages represented</p><p className="text-sm text-gray-200 mt-1">{new Set(data.documents.map((doc) => doc.stage)).size}</p></div>
              <div><p className="text-xs text-gray-500">Activity events</p><p className="text-sm text-gray-200 mt-1">{data.activity.length}</p></div>
            </div>
          </Card>
          <Card title="Chronological activity" description="Actor, action, timestamp, stage, and affected resource.">
            <div className="space-y-3">
              {data.activity.map((event) => (
                <div key={event.log_id} className="rounded-lg border border-border bg-background p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="active">{event.action}</Badge>
                      <span className="text-sm text-gray-200">{event.actor_name}</span>
                    </div>
                    <span className="text-xs text-gray-500">{new Date(event.timestamp).toLocaleString()}</span>
                  </div>
                  <p className="text-xs text-gray-400 mt-2">
                    {event.resource_type}{event.filename ? ` · ${event.filename}` : ''}
                    {event.document_stage ? ` · Stage: ${event.document_stage}` : ''}
                  </p>
                  {event.details && <p className="text-xs text-gray-500 mt-1">{event.details}</p>}
                </div>
              ))}
              {data.activity.length === 0 && <p className="text-sm text-gray-500">No project activity has been recorded yet.</p>}
            </div>
          </Card>
        </>
      )}
    </div>
  );
};

const UsersTab = ({ projects, currentUser, initialProjectId = '' }) => {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [expandedUserId, setExpandedUserId] = useState(null);
  const [userSearch, setUserSearch] = useState('');

  const [email, setEmail] = useState('');
  const [assignEmail, setAssignEmail] = useState('');
  const [assignProjectOpen, setAssignProjectOpen] = useState(false);
  const [newUserName, setNewUserName] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserTeam, setNewUserTeam] = useState('');
  const [projectId, setProjectId] = useState(initialProjectId);
  const [role, setRole] = useState('member');
  const [teamName, setTeamName] = useState('');
  const [busy, setBusy] = useState(false);
  const isOrgAdmin = Boolean(currentUser?.is_org_admin);
  const managedProjectRoles = currentUser?.project_roles || {};
  const canAssignProject = (projectId) => isOrgAdmin || ['admin', 'team_lead'].includes(managedProjectRoles[projectId]);
  const selectedProjectRole = managedProjectRoles[projectId];
  const isProjectAdmin = !isOrgAdmin && selectedProjectRole === 'admin';
  const isTeamLead = !isOrgAdmin && selectedProjectRole === 'team_lead';
  const canManageOrganization = isOrgAdmin;
  const roleOptions = isTeamLead
    ? [{ label: 'Member', value: 'member' }]
    : isProjectAdmin
      ? [{ label: 'Team Lead', value: 'team_lead' }, { label: 'Member', value: 'member' }]
    : isOrgAdmin
      ? [{ label: 'Project Admin', value: 'admin' }, { label: 'Team Lead', value: 'team_lead' }, { label: 'Member', value: 'member' }]
      : [{ label: 'Member', value: 'member' }];
  const teamOptions = isTeamLead && currentUser?.team_name
    ? [{ label: currentUser.team_name, value: currentUser.team_name }]
    : TEAM_OPTIONS;
  const requiresTeam = role === 'team_lead';
  const canSubmitAssignment = assignEmail.trim() && projectId && (!requiresTeam || teamName.trim());

  const load = async () => {
    setLoading(true);
    try {
      setUsers(await adminApi.listUsers());
    } catch (err) {
      setError(err.message || 'Could not load users.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleAssignProject = async () => {
    if (!assignEmail.trim() || !projectId) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await adminApi.assignProjectAccess({
        email: assignEmail.trim(),
        project_id: projectId,
        role,
        team_name: role === 'admin' ? null : (isTeamLead ? currentUser?.team_name : (teamName.trim() || null)),
      });
      setNotice(`Granted ${role} on ${projectId} to ${assignEmail}${teamName.trim() ? ` and assigned team ${teamName.trim()}` : ''}`);
      setAssignEmail('');
      setTeamName('');
      setAssignProjectOpen(false);
      await load();
    } catch (err) {
      setError(err.message || 'Could not assign access.');
    } finally {
      setBusy(false);
    }
  };

  const handleCreateUser = async () => {
    if (!email.trim() || !newUserName.trim()) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await adminApi.createUser({
        email: email.trim(),
        full_name: newUserName.trim(),
        team_name: newUserTeam.trim() || null,
      });
      setNotice(`${newUserName.trim()} was added to the organization and sent a welcome email.`);
      setEmail('');
      setNewUserName('');
      setNewUserTeam('');
      await load();
    } catch (err) {
      setError(err.message || 'Could not add the user.');
    } finally {
      setBusy(false);
    }
  };

  const handleGrantOrgAdmin = async (targetEmail) => {
    setBusy(true);
    setError('');
    try {
      await adminApi.assignAccess({ email: targetEmail, role: 'admin' });
      setNotice(`${targetEmail} is now an organization admin.`);
      await load();
    } catch (err) {
      setError(err.message || 'Could not grant admin access.');
    } finally {
      setBusy(false);
    }
  };

  const handleRevokeOrgAdmin = async (targetUser) => {
    if (!window.confirm(`Remove organization-admin access from ${targetUser.username}?`)) return;
    setBusy(true);
    setError('');
    try {
      await adminApi.revokeAccess(targetUser.user_id);
      setNotice(`${targetUser.username} is now a regular organization member.`);
      await load();
    } catch (err) {
      setError(err.message || 'Could not revoke admin access.');
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveUser = async (targetUser) => {
    if (!window.confirm(`Remove ${targetUser.username} from the organization? This removes all project access.`)) return;
    setBusy(true);
    setError('');
    try {
      await adminApi.removeUser(targetUser.user_id);
      setNotice(`${targetUser.username} was removed from the organization.`);
      await load();
    } catch (err) {
      setError(err.message || 'Could not remove the user.');
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveProject = async (targetUser, project) => {
    if (!project) {
      setError('Could not find the selected project.');
      return;
    }
    if (!window.confirm(`Remove ${targetUser.full_name || targetUser.username} from ${project.project_name}?`)) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await adminApi.removeProjectAccess(targetUser.user_id, project.project_id);
      setNotice(`${targetUser.username} was removed from ${project.project_name}.`);
      await load();
    } catch (err) {
      setError(err.message || 'Could not remove project access.');
    } finally {
      setBusy(false);
    }
  };

  const projectOptions = projects
    .filter((project) => canAssignProject(project.project_id))
    .map((project) => ({ label: project.project_name, value: project.project_id }));

  const openProjectAssignment = (targetEmail = '') => {
    setAssignEmail(targetEmail);
    setRole('member');
    setTeamName('');
    setAssignProjectOpen(true);
    window.requestAnimationFrame(() => {
      document.getElementById('assign-project-access')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  };

  return (
    <div className="space-y-6">
      <Modal
        open={assignProjectOpen}
        onClose={() => setAssignProjectOpen(false)}
        title="Assign project access"
        description={isTeamLead
          ? `Add a member to ${currentUser?.team_name || 'your team'} in an assigned project.`
          : 'Add a member to a project team or assign project-lead access.'}
        footer={(
          <>
            <Button type="button" variant="ghost" onClick={() => setAssignProjectOpen(false)}>Cancel</Button>
            <Button onClick={handleAssignProject} loading={busy} disabled={!canSubmitAssignment}>Assign</Button>
          </>
        )}
      >
        <div className="space-y-4">
          <Input label="User email" value={assignEmail} onChange={(e) => setAssignEmail(e.target.value)} placeholder="teammate@company.com" />
          <Dropdown
            label="Project"
            options={projectOptions}
            value={projectId}
            onChange={(value) => {
              setProjectId(value);
              if (!isOrgAdmin && managedProjectRoles[value] === 'team_lead') setRole('member');
            }}
            placeholder="Select project"
          />
          <Dropdown
            label="Role"
            options={roleOptions}
            value={role}
            onChange={(value) => {
              setRole(value);
              if (value === 'admin') setTeamName('');
            }}
          />
          {role !== 'admin' && (
            <Dropdown
              label="Team"
              options={teamOptions}
              value={isTeamLead ? (currentUser?.team_name || '') : teamName}
              onChange={setTeamName}
              placeholder={requiresTeam ? 'Select team for the lead' : 'Select team'}
              disabled={isTeamLead}
            />
          )}
          <p className="text-xs text-gray-500">
            {isTeamLead
              ? 'Team leads can add members only to their own team.'
              : 'Project admins can assign members to teams and designate team leads.'}
          </p>
        </div>
      </Modal>

      {canManageOrganization && <Card title="Add organization user" description="Create the account and send the member a welcome email with a temporary password.">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
          <Input label="Email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="new.user@company.com" />
          <Input label="Full name" value={newUserName} onChange={(e) => setNewUserName(e.target.value)} placeholder="New user" />
          <Dropdown label="Team" options={TEAM_OPTIONS} value={newUserTeam} onChange={setNewUserTeam} placeholder="Select team" />
          <Button onClick={handleCreateUser} loading={busy} disabled={!email.trim() || !newUserName.trim()}>Add user</Button>
        </div>
      </Card>}

      {notice && <p className="text-emerald-400 text-sm">{notice}</p>}
      {error && <p className="text-red-400 text-sm">{error}</p>}

      {isTeamLead && (
        <Card title="Team assignment scope" description="Team Lead access is limited to your assigned team and projects.">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-wider text-gray-500">Your team</p>
              <p className="text-base font-semibold text-gray-100 mt-1">{currentUser?.team_name || 'Team not assigned'}</p>
            </div>
            <p className="max-w-xl text-sm text-gray-400">
              Choose any other organization member below to add them as a Member of your team in an assigned Team Lead project.
            </p>
          </div>
        </Card>
      )}

      <Card>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-5 border-b border-border/50">
          <div>
            <h3 className="font-semibold text-gray-100">Users in your organization</h3>
            <p className="text-sm text-gray-400 mt-1">Project roles and org-admin status.</p>
          </div>
          <div className="w-full sm:w-64 sm:ml-auto shrink-0">
            <Input
              aria-label="Search organization users"
              value={userSearch}
              onChange={(event) => setUserSearch(event.target.value)}
              placeholder="Search names..."
              icon={Search}
            />
          </div>
        </div>
        {loading ? (
          <div className="flex justify-center py-8">
            <div className="w-6 h-6 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {users.filter((u) => {
              const query = userSearch.trim().toLowerCase();
              if (!query) return true;
              return [u.full_name, u.username, u.team_name]
                .filter(Boolean)
                .some((value) => value.toLowerCase().includes(query));
            }).filter((u) => u.user_id !== currentUser?.user_id).map((u) => (
              <div key={u.user_id} className="py-5 space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    {(!isOrgAdmin && u.is_org_admin) ? (
                      <span className="text-base font-semibold text-gray-200">{u.full_name || u.username}</span>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => setExpandedUserId((current) => current === u.user_id ? null : u.user_id)}
                          aria-expanded={expandedUserId === u.user_id}
                          className="text-left text-base font-semibold text-primary-light underline decoration-primary/50 underline-offset-4 hover:text-primary transition-colors"
                        >
                          {u.full_name || u.username}
                        </button>
                        <p className="text-sm text-gray-500 mt-1">{u.username}</p>
                        <div className="mt-2">
                          <Badge variant={u.team_name ? 'active' : 'inactive'}>
                            Current team: {u.team_name || 'Not assigned'}
                          </Badge>
                        </div>
                      </>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {(isOrgAdmin || !u.is_org_admin) && projects.some((project) => canAssignProject(project.project_id)) && (
                      <Button size="sm" variant="secondary" onClick={() => openProjectAssignment(u.username)} loading={busy}>
                        {isOrgAdmin ? 'Assign project' : isTeamLead ? 'Add to team' : 'Assign team role'}
                      </Button>
                    )}
                    {isOrgAdmin && (u.is_org_admin ? (
                      <div className="flex items-center gap-2">
                      <Badge variant="active">Org admin</Badge>
                      <Button size="sm" variant="secondary" onClick={() => handleRevokeOrgAdmin(u)} loading={busy}>Demote</Button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                      <Button size="sm" variant="secondary" onClick={() => handleGrantOrgAdmin(u.username)} loading={busy}>Promote</Button>
                      <Button size="sm" variant="secondary" onClick={() => handleRemoveUser(u)} loading={busy}>Remove</Button>
                      </div>
                    ))}
                  </div>
                </div>
                {(isOrgAdmin || !u.is_org_admin) && expandedUserId === u.user_id && (
                  <div className="rounded-lg border border-border/70 bg-background/40 overflow-x-auto">
                    <table className="w-full min-w-[520px] text-left">
                      <caption className="px-3 py-2 border-b border-border/50 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Project access
                      </caption>
                      <thead className="border-b border-border/50">
                        <tr className="text-xs text-gray-500">
                          <th className="px-3 py-2 font-medium">Project</th>
                          <th className="px-3 py-2 font-medium">Role</th>
                          <th className="px-3 py-2 font-medium text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {u.roles?.length ? u.roles.map((r) => (
                          <tr key={r.project_id} className="border-b border-border/30 last:border-0">
                            <td className="px-3 py-2.5">
                              <Link
                                to={`/projects/${encodeURIComponent(r.project_id)}`}
                                className="group block rounded-md p-1 -m-1 hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-primary/50"
                                aria-label={`Open project ${projects.find((project) => project.project_id === r.project_id)?.project_name || r.project_id}`}
                              >
                                <p className="text-sm text-primary-light group-hover:text-primary underline underline-offset-2 truncate">
                                  {projects.find((project) => project.project_id === r.project_id)?.project_name || r.project_id}
                                </p>
                                <p className="text-xs text-gray-600 mt-0.5">{r.project_id}</p>
                              </Link>
                            </td>
                            <td className="px-3 py-2.5"><Badge variant="neutral">{r.role}</Badge></td>
                            <td className="px-3 py-2.5 text-right">
                              {isOrgAdmin && (
                              <button
                                type="button"
                                onClick={() => handleRemoveProject(u, projects.find((project) => project.project_id === r.project_id))}
                                disabled={busy}
                                className="inline-flex items-center gap-1 rounded-md border border-red-500/30 px-2 py-1 text-xs text-red-400 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
                                title="Remove from project"
                              >
                                <Trash2 size={13} />
                                Remove
                              </button>
                              )}
                            </td>
                          </tr>
                        )) : (
                          <tr>
                            <td colSpan="3" className="px-3 py-3 text-sm text-gray-600">No project access assigned.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
            {users.length > 0 && !users.some((u) => {
              const query = userSearch.trim().toLowerCase();
              return !query || [u.full_name, u.username, u.team_name].filter(Boolean).some((value) => value.toLowerCase().includes(query));
            }) && (
              <p className="py-8 text-center text-sm text-gray-500">No users match “{userSearch}”.</p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
};

const ApprovalsTab = ({ projects }) => {
  const [projectId, setProjectId] = useState('');
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);

  const projectOptions = projects.map((p) => ({ label: p.project_name, value: p.project_id }));

  const load = async (id) => {
    if (!id) { setPending([]); return; }
    setLoading(true);
    setError('');
    try {
      setPending(await projectsApi.pendingApprovals(id));
    } catch (err) {
      setError(err.message || 'Could not load pending approvals.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(projectId); }, [projectId]);

  const handleApprove = async (documentId) => {
    setBusyId(documentId);
    try {
      await documentsApi.approve(documentId);
      await load(projectId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const handleReject = async (documentId) => {
    const reason = window.prompt('Rejection reason (min 5 characters):');
    if (!reason || reason.trim().length < 5) return;
    setBusyId(documentId);
    try {
      await documentsApi.reject(documentId, reason.trim());
      await load(projectId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="max-w-xs">
        <Dropdown label="Project" options={projectOptions} value={projectId} onChange={setProjectId} placeholder="Select a project" />
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {loading ? (
        <div className="flex justify-center py-8">
          <div className="w-6 h-6 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
        </div>
      ) : !projectId ? (
        <p className="text-gray-500 text-sm">Select a project to review documents awaiting approval.</p>
      ) : pending.length === 0 ? (
        <p className="text-gray-500 text-sm">Nothing pending review in this project.</p>
      ) : (
        <div className="space-y-3">
          {pending.map((doc) => (
            <div key={doc.document_id} className="flex items-center justify-between gap-4 p-4 bg-surface border border-border rounded-lg">
              <div>
                <p className="text-sm font-medium text-gray-200">{doc.filename}</p>
                <p className="text-xs text-gray-500">{doc.doc_type} &middot; {doc.stage}</p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" icon={Check} onClick={() => handleApprove(doc.document_id)} loading={busyId === doc.document_id}>Approve</Button>
                <Button size="sm" variant="danger" icon={X} onClick={() => handleReject(doc.document_id)} loading={busyId === doc.document_id}>Reject</Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const AuditTab = () => {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    adminApi.auditLog(200)
      .then(setEntries)
      .catch((err) => setError(err.message || 'Could not load the audit log.'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <div className="w-6 h-6 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (error) return <p className="text-red-400 text-sm">{error}</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b border-border">
            <th className="py-2 pr-4">Time</th>
            <th className="py-2 pr-4">Action</th>
            <th className="py-2 pr-4">Resource</th>
            <th className="py-2 pr-4">Details</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {entries.map((e) => (
            <tr key={e.log_id} className="text-gray-300">
              <td className="py-2 pr-4 whitespace-nowrap text-gray-500">{new Date(e.timestamp).toLocaleString()}</td>
              <td className="py-2 pr-4"><Badge variant="neutral">{e.action}</Badge></td>
              <td className="py-2 pr-4">{e.resource_type}{e.resource_id ? ` · ${e.resource_id}` : ''}</td>
              <td className="py-2 pr-4 text-gray-400">{e.details || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {entries.length === 0 && <p className="text-gray-500 text-sm py-8 text-center">No audit events yet.</p>}
    </div>
  );
};

const AbacTab = ({ projects }) => {
  const [matrix, setMatrix] = useState(null);
  const [projectId, setProjectId] = useState('');
  const [sensitivity, setSensitivity] = useState(1);
  const [workflowState, setWorkflowState] = useState('approved');
  const [visibleTeam, setVisibleTeam] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    adminApi.rbacMatrix().then(setMatrix).catch(() => {});
  }, []);

  const projectOptions = projects.map((p) => ({ label: p.project_name, value: p.project_id }));

  const handleSimulate = async () => {
    if (!projectId) return;
    setBusy(true);
    setError('');
    try {
      const res = await adminApi.simulateAbac({
        project_id: projectId,
        sensitivity_level: Number(sensitivity),
        workflow_state: workflowState,
        visible_to_teams: visibleTeam ? [visibleTeam] : [],
      });
      setResult(res);
    } catch (err) {
      setError(err.message || 'Simulation failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      {matrix && (
        <Card title="RBAC Role Matrix" description="Roles and the actions each one can perform.">
          <div className="space-y-3">
            {matrix.roles.map((r) => (
              <div key={r.role} className="p-3 bg-background rounded-lg border border-border">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-gray-100">{r.name}</p>
                  <span className="text-xs text-gray-500">{r.scope}</span>
                </div>
                <p className="text-xs text-gray-400 mt-1">{r.description}</p>
                <p className="text-xs text-primary-light mt-2">Team scope: {r.team_scope}</p>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {r.actions.map((a) => <Badge key={a} variant="neutral">{a}</Badge>)}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="ABAC Policy Simulator" description="See exactly why a hypothetical document would be allowed or denied for you.">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end mb-4">
          <Dropdown label="Project" options={projectOptions} value={projectId} onChange={setProjectId} placeholder="Select project" />
          <Dropdown
            label="Sensitivity level"
            options={[0, 1, 2, 3].map((v) => ({ label: String(v), value: String(v) }))}
            value={String(sensitivity)}
            onChange={(v) => setSensitivity(v)}
          />
          <Dropdown
            label="Workflow state"
            options={['draft', 'pending_review', 'approved', 'rejected'].map((v) => ({ label: v, value: v }))}
            value={workflowState}
            onChange={setWorkflowState}
          />
          <Dropdown
            label="Team visibility"
            options={[{ label: 'All teams', value: '' }, ...TEAM_OPTIONS]}
            value={visibleTeam}
            onChange={setVisibleTeam}
          />
        </div>
        <Button onClick={handleSimulate} disabled={!projectId} loading={busy}>Run Simulation</Button>

        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}

        {result && (
          <div className="mt-5 space-y-3">
            <div className={`p-3 rounded-lg border text-sm font-medium ${result.allowed ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400' : 'border-red-500/30 bg-red-500/10 text-red-400'}`}>
              {result.verdict}
            </div>
            {result.evaluations.map((ev) => (
              <div key={ev.rule_name} className="p-3 bg-background rounded-lg border border-border">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-gray-200">{ev.rule_name}</p>
                  <Badge variant={ev.passed ? 'success' : 'danger'}>{ev.passed ? 'PASS' : 'FAIL'}</Badge>
                </div>
                <p className="text-xs text-gray-500 mt-1">Required: {ev.required}</p>
                <p className="text-xs text-gray-500">Actual: {ev.actual}</p>
                <p className="text-xs text-gray-400 mt-1">{ev.explanation}</p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};

export default AdminPage;
