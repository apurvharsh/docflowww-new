import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Search as SearchIcon, UploadCloud, MessageSquare, ShieldCheck } from 'lucide-react';
import SourcePanel from '../components/sources/SourcePanel';
import ChatPanel from '../components/chat/ChatPanel';
import StudioPanel from '../components/studio/StudioPanel';
import Badge from '../components/ui/Badge';
import Modal from '../components/ui/Modal';
import Button from '../components/ui/Button';
import { projectsApi, agentsApi } from '../lib/api';
import { useAuth } from '../context/AuthContext';

const ProjectWorkspace = () => {
  const { projectId } = useParams();
  const { user } = useAuth();
  const [project, setProject] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [gapReport, setGapReport] = useState(null);
  const [gapLoading, setGapLoading] = useState(false);
  const [gapOpen, setGapOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [projects, docs] = await Promise.all([
        projectsApi.list(),
        projectsApi.documents(projectId),
      ]);
      setProject(projects.find((p) => p.project_id === projectId) || { project_id: projectId, project_name: projectId });
      setDocuments(docs);
    } catch (err) {
      setError(err.message || 'Could not load this project.');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAnalyzeGaps = async () => {
    setGapOpen(true);
    setGapLoading(true);
    try {
      const result = await agentsApi.analyzeGaps(projectId);
      setGapReport(result);
    } catch (err) {
      setGapReport({ gap_report: `Could not analyze gaps: ${err.message}` });
    } finally {
      setGapLoading(false);
    }
  };

  const role = user?.is_org_admin ? 'admin' : user?.project_roles?.[projectId];
  const canReview = role === 'admin' || role === 'reviewer';
  const canManageAccess = user?.is_org_admin || role === 'admin' || role === 'team_lead';
  const pendingCount = documents.filter((document) => document.workflow_state === 'pending_review').length;
  const approvedCount = documents.filter((document) => document.workflow_state === 'approved').length;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
        <h2 className="text-2xl font-bold text-gray-200 mb-2">Couldn't open this project</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <Link to="/" className="text-primary hover:underline">Return to Projects</Link>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col">
      {/* Workspace Header */}
      <div className="h-14 border-b border-border bg-background px-6 flex items-center gap-4 shrink-0">
        <Link to="/" className="text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1 text-sm">
          <ArrowLeft size={16} />
          All Projects
        </Link>
        <div className="w-px h-4 bg-border"></div>
        <h1 className="font-semibold text-gray-100">{project.project_name}</h1>
        {project.assigned_teams?.length > 0 && (
          <Badge variant="neutral">Team: {project.assigned_teams.join(', ')}</Badge>
        )}
        <Badge variant="active">{documents.length} doc{documents.length === 1 ? '' : 's'}</Badge>
        <div className="hidden xl:flex items-center gap-2">
          <Badge variant="success">{approvedCount} approved</Badge>
          {pendingCount > 0 && <Badge variant="warning">{pendingCount} pending review</Badge>}
        </div>
        <div className="flex-1" />
        {canManageAccess && (
          <Link to={`/admin?project_id=${encodeURIComponent(projectId)}`}>
            <Button size="sm" variant="secondary" icon={ShieldCheck}>
              {role === 'team_lead' && !user?.is_org_admin ? 'Manage team' : 'Manage access'}
            </Button>
          </Link>
        )}
        <Link to={`/projects/${projectId}/upload`}>
          <Button size="sm" variant="secondary" icon={UploadCloud}>Add sources</Button>
        </Link>
        <Link to={`/studio/query?project_id=${encodeURIComponent(projectId)}`}>
         <Button size="sm" variant="secondary" icon={MessageSquare}>Open Query Agent</Button>
        </Link>
        <Button size="sm" variant="secondary" icon={SearchIcon} onClick={handleAnalyzeGaps}>
          Analyze Gaps
        </Button>
      </div>

      {/* 3-Column Layout */}
      <div className="flex-1 flex flex-col lg:flex-row">
        <div className="w-full lg:w-[30%] shrink-0">
          <SourcePanel documents={documents} canReview={canReview} canDelete={user?.is_org_admin} onChanged={load} />
        </div>
        <div className="w-full lg:w-[40%] shrink-0 border-t border-border lg:border-t-0">
          <ChatPanel projectId={projectId} />
        </div>
        <div className="w-full lg:w-[30%] shrink-0 border-t border-border lg:border-t-0">
          <StudioPanel projectId={projectId} onAnalyzeGaps={handleAnalyzeGaps} />
        </div>
      </div>

      <Modal
        open={gapOpen}
        onClose={() => setGapOpen(false)}
        title="Documentation Gap Analysis"
        description="Gap-Detection Agent — compares uploaded documents against expected SDLC stage coverage."
      >
        {gapLoading ? (
          <div className="flex justify-center py-8">
            <div className="w-6 h-6 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
          </div>
        ) : (
          <div className="text-sm text-gray-300 whitespace-pre-wrap max-h-[50vh] overflow-y-auto">
            {gapReport?.gap_report}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default ProjectWorkspace;
