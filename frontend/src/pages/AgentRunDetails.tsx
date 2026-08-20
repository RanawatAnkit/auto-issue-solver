import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getAgentRun } from '../services/api';
import { AgentRunDetailResponse } from '../types/domain';
import { StatusBadge } from '../components/layout/StatusBadge';
import { ChevronDown, ChevronRight, PlayCircle, Clock, CheckCircle2, XCircle, Terminal, FileCode2 } from 'lucide-react';
import { cn } from '../utils/cn';

// Subcomponents
const ExpandableSection: React.FC<{ title: string; children: React.ReactNode; defaultOpen?: boolean; icon?: React.ReactNode }> = ({ title, children, defaultOpen = false, icon }) => {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="border border-slate-800 rounded-lg overflow-hidden bg-slate-900 shadow-sm">
            <button
                onClick={() => setOpen(!open)}
                className="w-full px-6 py-4 flex items-center justify-between bg-slate-800/40 hover:bg-slate-800/60 transition-colors font-medium text-slate-200"
            >
                <span className="flex items-center gap-2">{icon} {title}</span>
                {open ? <ChevronDown size={18} className="text-slate-500" /> : <ChevronRight size={18} className="text-slate-500" />}
            </button>
            {open && <div className="p-6 border-t border-slate-800">{children}</div>}
        </div>
    );
};

const JsonViewer: React.FC<{ data: any }> = ({ data }) => (
    <pre className="bg-slate-950 p-4 rounded text-sm text-emerald-400 overflow-x-auto border border-slate-800 shadow-inner">
        {JSON.stringify(data, null, 2)}
    </pre>
);

const TerminalLog: React.FC<{ data: any }> = ({ data }) => {
    const lines = data?.stdout ? data.stdout.split('\n') : [];
    const errors = data?.stderr ? data.stderr.split('\n') : [];
    return (
        <div className="bg-slate-950 rounded-lg border border-slate-800 shadow-inner font-mono text-xs overflow-hidden">
            <div className="bg-slate-900/50 p-3 border-b border-slate-800 text-slate-400 flex items-center justify-between">
                <span className="flex items-center gap-2"><Terminal size={14} /> Execution logs</span>
                {data?.success ? <span className="text-emerald-500">Success (Exit 0)</span> : <span className="text-rose-500">Failed</span>}
            </div>
            <div className="p-4 max-h-[400px] overflow-y-auto space-y-1">
                {lines.map((l: string, i: number) => <div key={`out-${i}`} className="text-slate-300 break-all">{l}</div>)}
                {errors.map((l: string, i: number) => <div key={`err-${i}`} className="text-rose-400 break-all">{l}</div>)}
            </div>
        </div>
    );
};

const DiffViewer: React.FC<{ diff: string }> = ({ diff }) => {
    if (!diff) return null;
    const lines = diff.split('\n');
    return (
        <div className="bg-slate-950 rounded border border-slate-800 overflow-hidden font-mono text-sm leading-relaxed shadow-inner max-h-[500px] overflow-y-auto">
            {lines.map((line, i) => {
                let color = 'text-slate-300';
                let bg = '';
                if (line.startsWith('+')) { color = 'text-emerald-400'; bg = 'bg-emerald-500/10'; }
                else if (line.startsWith('-')) { color = 'text-rose-400'; bg = 'bg-rose-500/10'; }
                else if (line.startsWith('@@')) color = 'text-indigo-400 font-semibold';

                return (
                    <div key={i} className={cn('px-4 py-0.5 min-w-max', color, bg)}>
                        {line || ' '}
                    </div>
                );
            })}
        </div>
    );
};

const TerminalStates = ['PR_CREATED', 'REPAIR_EXHAUSTED', 'TEST_FAILED', 'COMMIT_FAILED', 'PUSH_FAILED', 'PR_CREATION_FAILED', 'FAILED'];

const AgentRunDetails: React.FC = () => {
    const { runId } = useParams<{ runId: string }>();
    const [run, setRun] = useState<AgentRunDetailResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let interval: number;

        const fetchRun = async () => {
            try {
                if (!runId) return;
                const data = await getAgentRun(runId);
                setRun(data);
                setLoading(false);

                // Polling logic
                if (!TerminalStates.includes(data.status)) {
                    interval = window.setTimeout(fetchRun, 2500);
                }
            } catch (err: any) {
                setError(err.message);
                setLoading(false);
            }
        };

        fetchRun();
        return () => clearTimeout(interval);
    }, [runId]);

    if (loading && !run) return <div className="p-8 text-slate-400 animate-pulse">Loading execution details...</div>;
    if (error && !run) return <div className="p-8 text-rose-500">Error loading execution details: {error}</div>;
    if (!run) return null;

    return (
        <div className="p-8 max-w-5xl mx-auto space-y-8 pb-32">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-800/30 p-6 rounded-2xl border border-slate-700/50 shadow-sm">
                <div>
                    <h2 className="text-2xl font-bold text-white flex items-center gap-3">
                        Execution Run #{run.id}
                        <StatusBadge status={run.status} />
                    </h2>
                    <div className="text-slate-400 mt-2 flex items-center gap-6">
                        <span className="flex items-center gap-1.5"><PlayCircle size={14} /> Issue #{run.issue_id}</span>
                        <span className="flex items-center gap-1.5"><Clock size={14} /> {new Date(run.started_at).toLocaleString()}</span>
                        <span className="flex items-center gap-1.5"><Terminal size={14} /> Step: <strong>{run.current_step || 'Initializing'}</strong></span>
                    </div>
                </div>
                <div className="flex flex-col items-end text-sm">
                    <span className="text-slate-400 mb-1">Repair Attempts</span>
                    <span className="text-2xl font-bold text-white">{run.repair_attempt}</span>
                </div>
            </div>

            {run.error_message && (
                <div className="bg-rose-500/10 border border-rose-500/30 p-6 rounded-xl flex gap-4 text-rose-400">
                    <XCircle className="shrink-0 text-rose-500" />
                    <div>
                        <h3 className="font-semibold text-rose-500 mb-1">Execution Error</h3>
                        <p className="text-sm font-mono">{run.error_message}</p>
                    </div>
                </div>
            )}

            {/* Dynamic Sections */}
            <div className="space-y-4">
                {run.issue_analysis && (
                    <ExpandableSection title="Issue Analysis" defaultOpen icon={<FileCode2 size={16} />}>
                        <JsonViewer data={run.issue_analysis} />
                    </ExpandableSection>
                )}

                {run.solution_plan && (
                    <ExpandableSection title="Solution Plan" defaultOpen icon={<CheckCircle2 size={16} />}>
                        <JsonViewer data={run.solution_plan} />
                    </ExpandableSection>
                )}

                {run.modified_files && (
                    <ExpandableSection title="Modified Files" icon={<FileCode2 size={16} />}>
                        <ul className="space-y-2">
                            {Object.keys(run.modified_files).map(file => (
                                <li key={file} className="flex items-center gap-2 p-3 bg-slate-900 border border-slate-800 rounded font-mono text-sm text-indigo-300">
                                    <FileCode2 size={14} className="text-slate-500" /> {file}
                                </li>
                            ))}
                        </ul>
                    </ExpandableSection>
                )}

                {run.diff_summary && (
                    <ExpandableSection title="Code Diff Summary" defaultOpen icon={<FileCode2 size={16} />}>
                        <DiffViewer diff={run.diff_summary} />
                    </ExpandableSection>
                )}

                {run.test_result && (
                    <ExpandableSection title="Sandbox Test Execution" defaultOpen icon={<Terminal size={16} />}>
                        <TerminalLog data={run.test_result} />
                    </ExpandableSection>
                )}

                {run.failure_analysis && (
                    <ExpandableSection title="Failure Analysis (Auto-Repair)" icon={<XCircle size={16} className="text-rose-500" />}>
                        <JsonViewer data={run.failure_analysis} />
                    </ExpandableSection>
                )}

                {run.repair_plan && (
                    <ExpandableSection title="Repair Plan" icon={<CheckCircle2 size={16} className="text-amber-500" />}>
                        <JsonViewer data={run.repair_plan} />
                    </ExpandableSection>
                )}

                {run.repair_history && (
                    <ExpandableSection title="Repair History" icon={<Clock size={16} />}>
                        <JsonViewer data={run.repair_history} />
                    </ExpandableSection>
                )}

                {run.termination_reason && (
                    <div className="bg-slate-800/30 p-6 rounded-xl border border-slate-700/50 mt-8">
                        <h3 className="font-semibold text-slate-300 mb-2">Final Termination Status</h3>
                        <p className="text-slate-400">{run.termination_reason}</p>
                    </div>
                )}
            </div>
        </div>
    );
};

export default AgentRunDetails;
