import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Sparkles, Bot, ScanSearch, SearchCheck, MessageSquare, Users, Database, Plus } from 'lucide-react';
import { agentsApi, projectsApi } from '../lib/api';
import { useAuth } from '../context/AuthContext';
import Card from '../components/ui/Card';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Modal from '../components/ui/Modal';

const templates = [
  { id: 'prd', label: 'PRD' },
  { id: 'ard', label: 'ARD' },
  { id: 'test-plan', label: 'Test Plan' },
  { id: 'brd', label: 'BRD' },
];

const StudioPage = () => {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [gapReport, setGapReport] = useState(null);
  const [gapProject, setGapProject] = useState('');
  const [gapLoading, setGapLoading] = useState(false);
  const [teamProject, setTeamProject] = useState(null);
  const [projectSearch, setProjectSearch] = useState('');
  const { user } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    projectsApi.list()
      .then(setProjects)
      .catch((err) => setError(err.message || 'Could not load projects.'))
      .finally(() => setLoading(false));
  }, []);

  const analyzeGaps = async (projectId) => {
    setGapProject(projectId);
    setGapReport(null);
    setGapLoading(true);
    try {
      setGapReport(await agentsApi.analyzeGaps(projectId));
    } catch (err) {
      setGapReport({ gap_report: err.message || 'Could not analyze gaps.' });
    } finally {
      setGapLoading(false);
    }
  };

  const canAddTeam = (projectId) => Boolean(
    user?.is_org_admin || user?.project_roles?.[projectId] === 'admin'
  );

  const openTeamProject = (project) => {
    setTeamProject(project);
    setTeamName('');
    setTeamError('');
  };

  const filteredProjects = projects.filter((project) => {
    const query = projectSearch.trim().toLowerCase();
    if (!query) return true;
    return `${project.project_name || ''} ${project.description || ''}`.toLowerCase().includes(query);
  });

  return (
    <div className="flex-1 px-6 py-8 max-w-6xl mx-auto w-full">
      <div className="mb-8">
        <div className="flex items-center gap-2 text-primary text-xs font-bold uppercase tracking-widest mb-3"><Sparkles size={15} /> Studio</div>
        <h1 className="text-3xl font-bold text-gray-100">AI-powered document workspace</h1>
        <p className="text-gray-400 mt-2">Work independently in AI Studio or use your project context in Project AI Studio.</p>
      </div>

      <h2 className="text-lg font-semibold text-gray-100 mb-3">AI Studio</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8 items-stretch">
        <Link to="/studio/draft/prd" className="h-full"><Card className="h-full min-h-[180px]" title="Drafting Agent" description="Generate structured project documents from instructions." icon={Bot} /></Link>
        <Link to="/studio/scan" className="h-full"><Card className="h-full min-h-[180px]" title="Scanner Agent" description="Score and auto-revise generated drafts." icon={ScanSearch} /></Link>
        <Link to="/studio/query" className="h-full"><Card className="h-full min-h-[180px]" title="General Query Agent" description="Ask grounded questions across your project sources." icon={SearchCheck} /></Link>
      </div>

      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-lg font-semibold text-gray-100">Project AI Studio</h2>
        <label className="relative block w-full sm:w-72">
          <SearchCheck size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="search"
            value={projectSearch}
            onChange={(event) => setProjectSearch(event.target.value)}
            placeholder="Search projects..."
            aria-label="Search projects"
            className="w-full rounded-lg border border-border bg-surface py-2 pl-9 pr-3 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-500 focus:border-primary"
          />
        </label>
      </div>
      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}
      {loading ? (
        <div className="flex justify-center py-16"><div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" /></div>
      ) : projects.length === 0 ? (
        <Card title="No projects yet" description="Create a project before opening Studio." footer={<Link to="/" className="text-sm text-primary-light hover:underline">Go to Projects</Link>} />
      ) : filteredProjects.length === 0 ? (
        <Card title="No matching projects" description="Try a different project name or search term." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {filteredProjects.map((project) => (
            <Card key={project.project_id} className="h-full min-h-[190px]" title={project.project_name} description={project.description || 'Create and scan project documents with AI agents.'} icon={Sparkles}>
              <div className="flex flex-wrap items-center gap-2">
              <div className="w-full rounded-lg border border-border bg-background/60 px-3 py-2">
                <button
                  type="button"
                  onClick={() => openTeamProject(project)}
                  className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-primary-light transition-colors hover:text-primary"
                >
                  <Users size={14} /> Associated team
                </button>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => openTeamProject(project)}
                    className="text-left text-xs text-gray-500 transition-colors hover:text-gray-300"
                  >
                    {(project.members || []).length > 0
                      ? `${project.members.length} member${project.members.length === 1 ? '' : 's'} assigned`
                      : 'No members assigned yet.'}
                  </button>
                </div>
              </div>
              <p className="w-full text-xs font-bold text-gray-500 uppercase tracking-wider mb-1">Project agents</p>
                <Link to={`/projects/${project.project_id}/studio/prd`}><Button size="sm" variant="secondary" icon={Bot}>Draft</Button></Link>
                <Link to={`/projects/${project.project_id}/studio/scan`}><Button size="sm" variant="secondary" icon={ScanSearch}>Scan</Button></Link>
                <Link to={`/studio/query?project_id=${encodeURIComponent(project.project_id)}`}><Button size="sm" variant="secondary" icon={MessageSquare}>Query</Button></Link>
                <Link to={`/studio/query?agent=rag&project_id=${encodeURIComponent(project.project_id)}`}><Button size="sm" variant="secondary" icon={Database}>RAG</Button></Link>
                <Button size="sm" variant="secondary" icon={SearchCheck} onClick={() => analyzeGaps(project.project_id)}>Gaps</Button>
                <div className="w-full h-px bg-border my-1" />
                {templates.map((template) => (
                  <Link key={template.id} to={`/projects/${project.project_id}/studio/${template.id}`}>
                    <Badge className="hover:border-primary/50 hover:text-primary transition-colors cursor-pointer">{template.label}</Badge>
                  </Link>
                ))}
                <Link to={`/projects/${project.project_id}`} className="ml-auto inline-flex items-center gap-1 text-xs text-primary-light hover:text-primary">
                  Open project <ArrowRight size={14} />
                </Link>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal open={!!gapProject} onClose={() => setGapProject('')} title="Documentation Gap Analysis" description={gapProject}>
        {gapLoading ? <div className="flex justify-center py-8"><div className="w-6 h-6 border-4 border-primary/30 border-t-primary rounded-full animate-spin" /></div> : <div className="text-sm text-gray-300 whitespace-pre-wrap max-h-[50vh] overflow-y-auto">{gapReport?.gap_report}</div>}
      </Modal>

      <Modal
        open={!!teamProject}
        onClose={() => setTeamProject(null)}
        title="Associated team"
        description={teamProject ? `${teamProject.project_name} team members` : undefined}
        headerAction={teamProject && canAddTeam(teamProject.project_id) ? (
          <button
            type="button"
            onClick={() => {
              setTeamProject(null);
              navigate(`/admin?project_id=${encodeURIComponent(teamProject.project_id)}`);
            }}
            className="rounded-md p-1 text-gray-400 transition-colors hover:bg-primary/10 hover:text-primary"
            aria-label={`Assign roles for ${teamProject.project_name}`}
            title="Assign roles in this project"
          >
            <Plus size={18} />
          </button>
        ) : null}
      >
        {teamProject?.members?.length ? (
          <div className="max-h-[60vh] overflow-y-auto scrollbar-hidden space-y-3">
            {teamProject.members.map((member) => (
              <div key={member.user_id} className="rounded-lg border border-border bg-background/60 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="font-semibold text-gray-100">{member.name || 'Unnamed member'}</h3>
                    <p className="mt-1 text-sm text-primary-light">{member.email || member.username || 'No email available'}</p>
                  </div>
                  {member.role && <Badge variant="active">{member.role}</Badge>}
                </div>
                <dl className="mt-4 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                  <div><dt className="text-xs text-gray-500">Team</dt><dd className="text-gray-300">{member.team_name || teamProject.team_name || 'Not specified'}</dd></div>
                  <div><dt className="text-xs text-gray-500">Organization</dt><dd className="text-gray-300">{member.organization || 'Not specified'}</dd></div>
                  <div><dt className="text-xs text-gray-500">Job title</dt><dd className="text-gray-300">{member.job_title || 'Not specified'}</dd></div>
                  <div><dt className="text-xs text-gray-500">Username</dt><dd className="text-gray-300">{member.username || 'Not specified'}</dd></div>
                </dl>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">No team members are assigned to this project yet.</p>
        )}
      </Modal>
    </div>
  );
};

export default StudioPage;
