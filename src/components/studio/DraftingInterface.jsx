import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Check, Sparkles, ScanSearch, Save, Download } from 'lucide-react';
import Button from '../ui/Button';
import Textarea from '../ui/Textarea';
import Dropdown from '../ui/Dropdown';
import Badge from '../ui/Badge';
import { API_BASE, documentsApi, projectsApi, studioApi } from '../../lib/api';
import { STAGES } from '../../constants/stages';

const TEMPLATE_META = {
  prd: { title: 'Product Requirements Document (PRD)', docType: 'PRD' },
  ard: { title: 'Architecture / Design Document (ARD)', docType: 'Design Document' },
  'test-plan': { title: 'Quality Assurance Test Plan', docType: 'Test Plan' },
  brd: { title: 'Business Requirements Document (BRD)', docType: 'BRD' },
};

const stageOptions = STAGES.map((s) => ({ label: s, value: s }));

const cleanInlineMarkdown = (line) => line
  .replace(/&amp;/g, '&')
  .replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>')
  .replace(/\*\*(.*?)\*\*/g, '$1')
  .replace(/__(.*?)__/g, '$1')
  .replace(/`([^`]+)`/g, '$1')
  .trim();

const tableCells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cleanInlineMarkdown(cell));
const isTableSeparator = (line) => tableCells(line).length > 0 && tableCells(line).every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line) && line.split('|').length >= 3;

const renderDraftLines = (lines) => {
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const key = `${index}-${line}`;

    if (/^\s*```/.test(line)) {
      const language = line.replace(/^\s*```/, '').trim();
      const code = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      blocks.push(
        <pre key={key} className="my-4 overflow-x-auto rounded-lg border border-gray-300 bg-gray-900 p-4 text-xs leading-5 text-gray-100">
          <code data-language={language || undefined}>{code.join('\n')}</code>
        </pre>,
      );
      index += 1;
      continue;
    }

    if (isTableRow(line) && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const headers = tableCells(line);
      const rows = [];
      index += 2;
      while (index < lines.length) {
        if (!lines[index].trim()) {
          index += 1;
          continue;
        }
        if (!isTableRow(lines[index]) || isTableSeparator(lines[index])) break;
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push(
        <div key={key} className="my-5 overflow-x-auto rounded-lg border border-gray-300">
          <table className="min-w-full border-collapse text-left text-sm">
            <thead className="bg-gray-100">
              <tr>{headers.map((cell, cellIndex) => <th key={`${key}-h-${cellIndex}`} className="border-b border-gray-300 px-3 py-2 font-semibold">{cell}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${key}-r-${rowIndex}`} className="even:bg-gray-50">
                  {headers.map((_, cellIndex) => <td key={`${key}-${rowIndex}-${cellIndex}`} className="border-b border-gray-200 px-3 py-2 align-top">{row[cellIndex] || ''}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^\s*([-*_])\s*\1\s*\1\s*$/.test(line)) {
      blocks.push(<div key={key} className="h-4" />);
    } else if (/^###\s+/.test(line)) {
      blocks.push(<h3 key={key} className="mt-5 text-base font-semibold text-gray-900">{cleanInlineMarkdown(line.replace(/^###\s+/, ''))}</h3>);
    } else if (/^##\s+/.test(line)) {
      blocks.push(<h2 key={key} className="mt-7 border-b border-gray-200 pb-1 text-lg font-semibold text-gray-900">{cleanInlineMarkdown(line.replace(/^##\s+/, ''))}</h2>);
    } else if (/^#\s+/.test(line)) {
      blocks.push(<h1 key={key} className="mb-6 border-b-2 border-gray-900 pb-3 text-2xl font-bold text-gray-950">{cleanInlineMarkdown(line.replace(/^#\s+/, ''))}</h1>);
    } else if (/^[-*]\s+/.test(line)) {
      blocks.push(<li key={key} className="ml-5 list-disc pl-1">{cleanInlineMarkdown(line.replace(/^[-*]\s+/, ''))}</li>);
    } else if (/^\d+\.\s+/.test(line)) {
      blocks.push(<li key={key} className="ml-5 list-decimal pl-1">{cleanInlineMarkdown(line.replace(/^\d+\.\s+/, ''))}</li>);
    } else if (!line.trim()) {
      blocks.push(<div key={key} className="h-3" />);
    } else {
      blocks.push(<p key={key} className="leading-6">{cleanInlineMarkdown(line)}</p>);
    }
    index += 1;
  }
  return blocks;
};

const DraftingInterface = () => {
  const { projectId, templateId } = useParams();
  const meta = TEMPLATE_META[templateId] || { title: 'Document', docType: 'Other' };

  const [stage, setStage] = useState('Requirements');
  const [instructions, setInstructions] = useState('');
  const [draft, setDraft] = useState(null);
  const [draftStage, setDraftStage] = useState(null);
  const [isGenerating, setIsGenerating] = useState(false);

  const [scanResult, setScanResult] = useState(null);
  const [isScanning, setIsScanning] = useState(false);

  const [isSaving, setIsSaving] = useState(false);
  const [savedPath, setSavedPath] = useState(null);
  const [savedDrafts, setSavedDrafts] = useState([]);
  const [isDraftsOpen, setIsDraftsOpen] = useState(false);
  const [isLoadingDrafts, setIsLoadingDrafts] = useState(false);
  const [isLoadingSaved, setIsLoadingSaved] = useState(false);
  const [isUploadingProject, setIsUploadingProject] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (projectId) {
      projectsApi.list()
        .then((items) => {
          const project = items.find((item) => item.project_id === projectId);
          setProjectName(project?.project_name || projectId);
        })
        .catch(() => setProjectName(projectId));
    }
  }, [projectId]);

  const handleGenerate = async () => {
    if (!instructions.trim()) return;
    setIsGenerating(true);
    setError('');
    setScanResult(null);
    setSavedPath(null);
    try {
      const result = await studioApi.generateDraft({
        document_type: meta.docType,
        project_stage: stage,
        instructions,
        project_id: projectId || null,
        current_content: draft && draftStage === stage ? draft : null,
      });
      setDraft(result.draft);
      setDraftStage(stage);
    } catch (err) {
      setError(err.message || 'Could not generate a draft.');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleScan = async () => {
    if (!draft) return;
    setIsScanning(true);
    setError('');
    try {
      const result = await studioApi.scanDraft({
        document_text: draft,
        document_type: meta.docType,
        project_stage: stage,
      });
      setScanResult(result);
    } catch (err) {
      setError(err.message || 'Could not scan the draft.');
    } finally {
      setIsScanning(false);
    }
  };

  const applyRevision = () => {
    if (scanResult?.revised_document) {
      setDraft(scanResult.revised_document);
      setScanResult(null);
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setIsSaving(true);
    setError('');
    try {
      const result = await studioApi.saveDraft({
        document_text: draft,
        document_type: meta.docType,
        project_stage: stage,
        project_id: projectId || null,
      });
      setSavedPath(result.filename);
      setSavedDrafts((current) => current.includes(result.filename)
        ? current
        : [result.filename, ...current]);
    } catch (err) {
      setError(err.message || 'Could not save the draft.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleViewSaved = async (filename = savedPath) => {
    if (!filename) return;
    setSavedPath(filename);
    setIsLoadingSaved(true);
    setError('');
    try {
      const savedContent = await studioApi.savedDraft(filename);
      setDraft(savedContent);
      setDraftStage(stage);
      setInstructions('');
    } catch (err) {
      setError(err.message || 'Could not open the saved draft.');
    } finally {
      setIsLoadingSaved(false);
    }
  };

  const handleOpenDrafts = async () => {
    setIsDraftsOpen((current) => !current);
    if (savedDrafts.length) return;

    setIsLoadingDrafts(true);
    setError('');
    try {
      const result = await studioApi.savedDrafts();
      setSavedDrafts(result.drafts || []);
    } catch (err) {
      setError(err.message || 'Could not load saved drafts.');
    } finally {
      setIsLoadingDrafts(false);
    }
  };

  const handleUploadToProject = async () => {
    if (!draft || !projectId) return;
    setIsUploadingProject(true);
    setError('');
    try {
      const filename = savedPath || `${meta.docType.toLowerCase().replace(/\s+/g, '-')}-${stage.toLowerCase().replace(/\s+/g, '-')}.md`;
      const formData = new FormData();
      formData.append('file', new Blob([draft], { type: 'text/markdown' }), filename);
      formData.append('project_id', projectId);
      formData.append('project_name', projectName || projectId);
      formData.append('stage', stage);
      formData.append('doc_type', meta.docType);
      formData.append('visible_to_teams', '');
      formData.append('sensitivity_level', '1');
      await documentsApi.upload(formData);
      setSavedPath((current) => current || filename);
    } catch (err) {
      setError(err.message || 'Could not upload the draft to the project.');
    } finally {
      setIsUploadingProject(false);
    }
  };

  const handleDownloadWord = async () => {
    if (!draft) return;
    setError('');
    try {
      const response = await fetch(`${API_BASE}/studio/download-draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('docflow_token') || ''}` },
        body: JSON.stringify({ document_text: draft, document_type: meta.docType, project_stage: stage, project_id: projectId || null }),
      });
      if (!response.ok) {
        const responseText = await response.text();
        let detail = response.statusText || 'Could not download the Word document.';
        try {
          detail = JSON.parse(responseText).detail || detail;
        } catch {
          if (responseText) detail = responseText;
        }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${meta.docType.toLowerCase().replace(/\s+/g, '-')}-${stage.toLowerCase().replace(/\s+/g, '-')}.docx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || 'Could not download the Word document.');
    }
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col overflow-hidden bg-background">
      <style>{`
        .a4-page {
          box-sizing: border-box;
          width: 100%;
          max-width: 794px;
          aspect-ratio: 210 / 297;
          height: auto;
          flex: 0 0 auto;
          margin: 0 auto;
          padding: clamp(24px, 6%, 68px);
          background: #ffffff;
          box-shadow: 0 8px 24px rgba(17, 24, 39, 0.12);
          font-size: clamp(10px, 1.35vw, 13px);
          overflow: hidden;
          overflow-wrap: anywhere;
        }
        .draft-pages {
          display: flex;
          flex-direction: column;
          gap: 24px;
        }
        .draft-preview-scroll {
          scrollbar-width: none;
          -ms-overflow-style: none;
        }
        .draft-preview-scroll::-webkit-scrollbar {
          display: none;
        }
        @media print {
          body * { visibility: hidden; }
          @page { size: A4; margin: 10mm; }
          .draft-print-document, .draft-print-document * { visibility: visible; }
          .draft-print-document { position: absolute; inset: 0; display: block !important; background: #e5e7eb; color: #111827; }
          .draft-pages { display: block; }
          .a4-page { width: 190mm !important; min-height: 277mm !important; height: 277mm !important; margin: 0 auto !important; padding: 18mm !important; font-size: 11pt; box-shadow: none !important; page-break-after: always; overflow: hidden; }
          .a4-page:last-child { page-break-after: auto; }
          .draft-print-document h1 { color: #111827 !important; }
        }
      `}</style>
      <div className="no-print h-14 border-b border-border bg-background px-6 flex items-center justify-between shrink-0">
        <Link to={projectId ? `/projects/${projectId}` : '/studio'} className="text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1 text-sm">
          <ArrowLeft size={16} />
          {projectId ? 'Back to Project' : 'Back to Studio'}
        </Link>
        <div className="flex items-center gap-2">
          {draft && (
            <Button size="sm" variant="secondary" icon={ScanSearch} onClick={handleScan} loading={isScanning}>
              Scan Draft
            </Button>
          )}
          {draft && (
            <Button size="sm" onClick={handleSave} disabled={isSaving} loading={isSaving}>
              {savedPath ? <><Check size={16} className="mr-1" /> Saved</> : <><Save size={14} className="mr-1" /> Save Draft</>}
            </Button>
          )}
          <div className="relative">
            <Button size="sm" variant="secondary" onClick={handleOpenDrafts} loading={isLoadingDrafts}>
              Drafts
            </Button>
            {isDraftsOpen && (
              <div className="absolute right-0 top-10 z-20 w-64 rounded-lg border border-border bg-surface p-2 shadow-xl">
                <p className="px-2 py-1 text-xs font-medium text-gray-400">Saved drafts</p>
                {savedDrafts.length ? (
                  <div className="max-h-56 overflow-y-auto">
                    {savedDrafts.map((filename) => (
                      <button
                        key={filename}
                        type="button"
                        className="block w-full rounded-md px-2 py-2 text-left text-xs text-gray-300 hover:bg-surface-hover hover:text-white"
                        onClick={() => {
                          setIsDraftsOpen(false);
                          handleViewSaved(filename);
                        }}
                      >
                        {filename}
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="px-2 py-2 text-xs text-gray-500">No saved drafts yet.</p>
                )}
              </div>
            )}
          </div>
          {draft && projectId && (
            <Button size="sm" variant="secondary" onClick={handleUploadToProject} loading={isUploadingProject}>
              Upload to Project
            </Button>
          )}
          {draft && (
            <Button size="sm" variant="secondary" icon={Download} onClick={handleDownloadWord}>
              Download Word
            </Button>
          )}
        </div>
      </div>

      <div className="draft-print-area mx-auto grid min-h-0 w-full max-w-6xl flex-1 grid-cols-1 items-stretch gap-6 overflow-hidden p-6 md:grid-cols-2 md:grid-rows-[minmax(0,1fr)]">
        {/* Left Column: Instructions */}
        <div className="flex min-h-0 w-full flex-col gap-5">
          <div>
            <h1 className="text-2xl font-bold text-gray-100">Draft {meta.title}</h1>
            <p className="text-gray-400 mt-1">The Drafting Agent turns instructions into a structured first draft.</p>
          </div>

          <Dropdown label="Project stage" options={stageOptions} value={stage} onChange={setStage} />

          <div className="flex flex-col">
            <Textarea
              label="Drafting Instructions"
              placeholder="Describe what you want to create... (e.g. Create a PRD for an AI chat feature)"
              className="min-h-[160px]"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
            <Button
              className="mt-4 w-full"
              onClick={handleGenerate}
              loading={isGenerating}
              disabled={!instructions.trim() || isGenerating}
              icon={Sparkles}
            >
              {draft ? 'Regenerate Draft' : 'Generate Draft'}
            </Button>
            {error && <p className="text-red-400 text-sm text-center mt-3">{error}</p>}
            {savedPath && (
              <div className="mt-3 flex flex-col items-center gap-2">
                <p className="text-emerald-400 text-sm text-center">Saved to backend/drafts/{savedPath}</p>
                {projectId && (
                  <Button size="sm" variant="secondary" onClick={handleUploadToProject} loading={isUploadingProject}>
                    Upload to Project
                  </Button>
                )}
              </div>
            )}
          </div>

          {scanResult && (
            <div className="border border-border rounded-xl bg-surface p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-200">Scanner Agent result</h3>
                <Badge variant={scanResult.score.passed ? 'success' : 'warning'}>
                  {scanResult.score.total}/60 pts
                </Badge>
              </div>
              <p className="text-xs text-gray-400">{scanResult.score.summary}</p>
              <div className="space-y-1.5">
                {scanResult.score.criteria.map((c) => (
                  <div key={c.name} className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">{c.name}</span>
                    <span className={c.score >= c.minimum ? 'text-emerald-400' : 'text-amber-400'}>{c.score}/{c.max_score}</span>
                  </div>
                ))}
              </div>
              {scanResult.revised_document && (
                <Button size="sm" variant="secondary" className="w-full" onClick={applyRevision}>
                  Use suggested revision
                </Button>
              )}
            </div>
          )}
        </div>

        {/* Right Column: Preview */}
        <div className="draft-print-document flex min-h-0 w-full flex-col">
          <div className="flex h-full min-h-0 w-full flex-col rounded-xl border border-border bg-surface">
            <div className="p-3 border-b border-border/50 bg-background/50">
              <h3 className="text-sm font-medium text-gray-300">Draft Preview</h3>
            </div>
            <div className="draft-preview-scroll min-h-0 flex-1 overflow-y-auto bg-gray-200 p-4 text-gray-900">
              {isGenerating ? (
                <div className="h-full flex flex-col items-center justify-center text-gray-400 gap-4">
                  <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
                  <p>Generating draft...</p>
                </div>
              ) : draft ? (
                <article className="font-sans text-[15px]">
                  {renderDraftLines(draft.split('\n'))}
                </article>
              ) : (
                <div className="h-full flex items-center justify-center text-gray-400 text-center px-8">
                  <p>Pick a stage, enter instructions, and click generate to see a preview of your {meta.title}.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DraftingInterface;
