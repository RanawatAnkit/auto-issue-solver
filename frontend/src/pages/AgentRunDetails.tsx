import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { errorMessage, getAgentRun, getAgentRunActions, rerunAgentRun } from '../services/api';
import { AgentAction, AgentRunDetailResponse, TestResult } from '../types/domain';
import { StatusBadge } from '../components/layout/StatusBadge';
import { ChevronDown, ChevronRight, PlayCircle, Clock, CheckCircle2, XCircle, Terminal, FileCode2, RotateCcw, ListChecks } from 'lucide-react';
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

const JsonViewer: React.FC<{ data: unknown }> = ({ data }) => (
    <pre className="bg-slate-950 p-4 rounded text-sm text-emerald-400 overflow-x-auto border border-slate-800 shadow-inner">
        {JSON.stringify(data, null, 2)}
    </pre>
);

const OUTCOME_LABELS: Record<string, string> = {
    PASSED: 'Tests passed',
    TESTS_FAILED: 'Tests failed',
    COLLECTION_ERROR: 'Tests could not be collected (import/syntax error)',
    NO_TESTS: 'No tests were found',
    DEPENDENCY_ERROR: 'Sandbox is missing a dependency',
    ENVIRONMENT_ERROR: 'Sandbox could not run the tests',
    TIMEOUT: 'Tests timed out',
};

const TERMINATION_REASONS: Record<string, string> = {
    MAX_ATTEMPTS: 'The repair loop used all of its attempts without making the tests pass.',
    REPEATED_FAILURE: 'A repair attempt reproduced an earlier failure exactly, so further attempts were stopped.',
    ENVIRONMENT_FAILURE: 'The sandbox could not run the tests (missing test runner or dependencies). This is an environment problem, not evidence about the generated code.',
    TIME_BUDGET: 'The repair loop ran out of its time budget.',
    LLM_ERROR: 'The language model failed to produce a valid analysis or plan.',
    NO_VALID_PATCH: 'Every generated edit for a file was rejected (out of scope, invalid syntax, or not matching the file). No change was applied; the error lists each attempt.',
};

const testSummary = (data: TestResult) => {
    const counts = Object.entries(data.summary || {}).map(([k, v]) => `${v} ${k}`).join(', ');
    return [counts, data.duration_seconds ? `${data.duration_seconds}s` : ''].filter(Boolean).join(' · ');
};

const TerminalLog: React.FC<{ data: TestResult }> = ({ data }) => {
    const lines = data?.stdout ? data.stdout.split('\n') : [];
    const errors = data?.stderr ? data.stderr.split('\n') : [];
    const label = (data?.outcome && OUTCOME_LABELS[data.outcome]) || (data?.success ? 'Success' : 'Failed');
    return (
        <div className="bg-slate-950 rounded-lg border border-slate-800 shadow-inner font-mono text-xs overflow-hidden">
            <div className="bg-slate-900/50 p-3 border-b border-slate-800 text-slate-400 flex items-center justify-between gap-4">
                <span className="flex items-center gap-2 truncate"><Terminal size={14} /> {data?.command?.join(' ') || 'Execution logs'}</span>
                <span className={cn('shrink-0', data?.success ? 'text-emerald-500' : 'text-rose-500')}>
                    {label} (exit {data?.exit_code}){testSummary(data) && ` · ${testSummary(data)}`}
                </span>
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

// Must match TERMINAL_AGENT_STATES in the backend. TEST_FAILED is *not* terminal:
// the repair loop starts from it.
const TerminalStates = ['COMPLETED', 'PR_CREATED', 'REPAIR_EXHAUSTED', 'COMMIT_FAILED', 'PUSH_FAILED', 'PR_CREATION_FAILED', 'FAILED', 'PLAN_READY', 'VALIDATION_FAILED', 'BRANCH_CREATION_FAILED', 'CANCELLED'];

const AgentRunDetails: React.FC = () => {
    const { runId } = useParams<{ runId: string }>();
    const navigate = useNavigate();
    const [run, setRun] = useState<AgentRunDetailResponse | null>(null);
    const [actions, setActions] = useState<AgentAction[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [rerunError, setRerunError] = useState<string | null>(null);
    const [rerunning, setRerunning] = useState(false);

    useEffect(() => {
        let timer: number | undefined;
        let cancelled = false;

        const fetchRun = async () => {
            if (!runId) return;
            let terminal = false;
            try {
                const [data, log] = await Promise.all([getAgentRun(runId), getAgentRunActions(runId)]);
                if (cancelled) return;
                setRun(data);
                setActions(log);
                setError(null);
                terminal = TerminalStates.includes(data.status);
            } catch (err) {
                if (cancelled) return;
                setError(errorMessage(err, 'Failed to load run'));
            } finally {
                if (!cancelled) setLoading(false);
            }
            // Keep polling through transient errors until the run reaches a terminal state.
            if (!cancelled && !terminal) timer = window.setTimeout(fetchRun, 3000);
        };

        fetchRun();
        return () => { cancelled = true; clearTimeout(timer); };
    }, [runId]);

    const handleRerun = async () => {
        if (!runId) return;
        setRerunning(true);
        setRerunError(null);
        try {
            const res = await rerunAgentRun(runId);
            navigate(`/agent-runs/${res.run_id}`);
        } catch (err) {
            setRerunError(errorMessage(err, 'Could not start a new run'));
        } finally {
            setRerunning(false);
        }
    };

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
                <div className="flex flex-col items-end text-sm gap-2">
                    <span className="text-slate-400">Repair Attempts</span>
                    <span className="text-2xl font-bold text-white">{run.repair_attempt ?? 0}</span>
                    {TerminalStates.includes(run.status) && (
                        <button
                            onClick={handleRerun}
                            disabled={rerunning}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-100 disabled:opacity-50"
                        >
                            <RotateCcw size={14} /> {rerunning ? 'Starting…' : 'Re-run'}
                        </button>
                    )}
                </div>
            </div>

            {rerunError && (
                <div className="bg-rose-500/10 border border-rose-500/30 px-4 py-3 rounded-lg text-sm text-rose-400">{rerunError}</div>
            )}
            {error && (
                <div className="bg-amber-500/10 border border-amber-500/30 px-4 py-3 rounded-lg text-sm text-amber-400">
                    Could not refresh this run ({error}); retrying…
                </div>
            )}

            {run.termination_reason && (
                <div className="bg-slate-800/30 p-6 rounded-xl border border-slate-700/50">
                    <h3 className="font-semibold text-slate-300 mb-1">Why the run stopped: {run.termination_reason}</h3>
                    <p className="text-slate-400 text-sm">{TERMINATION_REASONS[run.termination_reason] ?? ''}</p>
                </div>
            )}

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

                {run.modified_files && run.modified_files.length > 0 && (
                    <ExpandableSection title={`Modified Files (${run.modified_files.length})`} icon={<FileCode2 size={16} />}>
                        <ul className="space-y-2">
                            {run.modified_files.map(file => (
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

                {run.baseline_test_result && (
                    <ExpandableSection title="Baseline Tests (before any change)" icon={<Terminal size={16} />}>
                        <TerminalLog data={run.baseline_test_result} />
                    </ExpandableSection>
                )}

                {run.test_result && (
                    <ExpandableSection title="Sandbox Test Execution (latest)" defaultOpen icon={<Terminal size={16} />}>
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

                {run.repair_history && run.repair_history.length > 0 && (
                    <ExpandableSection title={`Repair History (${run.repair_history.length} attempts)`} icon={<Clock size={16} />}>
                        <ol className="space-y-3 mb-4">
                            {run.repair_history.map((h) => (
                                <li key={h.attempt} className="p-3 bg-slate-900 border border-slate-800 rounded text-sm">
                                    <div className="font-medium text-slate-200">Attempt {h.attempt}</div>
                                    {h.target_files && <div className="text-slate-400 font-mono text-xs mt-1">Files: {h.target_files.join(', ') || '—'}</div>}
                                    {h.test_result && (
                                        <div className={cn('mt-1', h.test_result.success ? 'text-emerald-400' : 'text-rose-400')}>
                                            {(h.test_result.outcome && OUTCOME_LABELS[h.test_result.outcome]) || (h.test_result.success ? 'Passed' : 'Failed')}
                                            {testSummary(h.test_result) && ` · ${testSummary(h.test_result)}`}
                                        </div>
                                    )}
                                    {h.error && <div className="mt-1 text-amber-400 break-words">{h.error}</div>}
                                </li>
                            ))}
                        </ol>
                        <JsonViewer data={run.repair_history} />
                    </ExpandableSection>
                )}

                {actions.length > 0 && (
                    <ExpandableSection title="Step Log" icon={<ListChecks size={16} />}>
                        <ul className="space-y-2">
                            {actions.map((a) => (
                                <li key={a.id} className="p-3 bg-slate-900 border border-slate-800 rounded text-sm">
                                    <div className="flex items-center gap-2">
                                        {a.success === false ? <XCircle size={14} className="text-rose-500" /> : <CheckCircle2 size={14} className="text-emerald-500" />}
                                        <span className="font-mono text-slate-200">{a.action_type}</span>
                                        {a.created_at && <span className="text-slate-500 text-xs ml-auto">{new Date(a.created_at).toLocaleTimeString()}</span>}
                                    </div>
                                    {a.output_data && <pre className="mt-2 text-xs text-slate-400 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{a.output_data}</pre>}
                                </li>
                            ))}
                        </ul>
                    </ExpandableSection>
                )}
            </div>
        </div>
    );
};

export default AgentRunDetails;
