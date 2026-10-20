import React, { useCallback, useEffect, useState } from 'react';
import {
    getBenchmarkTasks,
    getEvaluationStats,
    getEvaluationStatus,
    stopEvaluationRun,
    triggerEvaluationRun,
    errorMessage
} from '../services/api';
import {
    BenchmarkTask,
    EvaluationJobStatus,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationStats
} from '../types/domain';
import {
    Award,
    Play,
    RotateCcw,
    CheckCircle2,
    Clock,
    Cpu,
    FileCode,
    GitBranch,
    Copy,
    Check,
    X,
    Search,
    AlertCircle,
    FileText,
    ListChecks,
    CheckSquare,
    Eye,
    Code,
    ShieldCheck,
    Square
} from 'lucide-react';
import { cn } from '../utils/cn';

type Tab = 'description' | 'result' | 'tests' | 'patch';
type Filter = 'ALL' | 'RESOLVED' | 'AGENT_FAILED' | 'INFRA' | 'NOT_RUN';

const AGENT_FAILURES: EvaluationOutcome[] = ['UNRESOLVED', 'NO_PATCH', 'PATCH_APPLY_FAILED'];
const INFRA_FAILURES: EvaluationOutcome[] = ['LLM_ERROR', 'ENV_ERROR', 'HARNESS_ERROR'];

const OUTCOME_HELP: Record<EvaluationOutcome, string> = {
    RESOLVED: 'All FAIL_TO_PASS and PASS_TO_PASS tests passed with the patch.',
    UNRESOLVED: 'The patch was tested, but some required tests failed.',
    NO_PATCH: 'The agent produced no applicable change (invalid plan, rejected edits or no edit).',
    PATCH_APPLY_FAILED: 'The patch did not apply to a clean checkout.',
    LLM_ERROR: 'The LLM provider failed (quota, key, outage). Not an agent result.',
    ENV_ERROR: 'The test environment could not run (Docker, install, network). Not an agent result.',
    HARNESS_ERROR: 'Unexpected harness error. Not an agent result.',
};

const OutcomeBadge: React.FC<{ outcome?: EvaluationOutcome | null; label?: string }> = ({ outcome, label }) => {
    let colorClass = 'bg-slate-700/50 text-slate-400 border border-slate-700';
    if (outcome === 'RESOLVED') {
        colorClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
    } else if (outcome && AGENT_FAILURES.includes(outcome)) {
        colorClass = 'bg-rose-500/20 text-rose-400 border border-rose-500/30';
    } else if (outcome && INFRA_FAILURES.includes(outcome)) {
        colorClass = 'bg-amber-500/20 text-amber-400 border border-amber-500/30';
    }
    return (
        <span
            className={cn('px-2.5 py-0.5 rounded-full text-xs font-medium tracking-wide whitespace-nowrap', colorClass)}
            title={outcome ? OUTCOME_HELP[outcome] : 'Not evaluated yet'}
        >
            {label ?? (outcome ? outcome.replace(/_/g, ' ') : 'NOT RUN')}
        </span>
    );
};

const EnvBadge: React.FC<{ check: EvaluationResult | null }> = ({ check }) => {
    if (!check) return <OutcomeBadge outcome={null} label="UNCHECKED" />;
    if (check.outcome === 'RESOLVED') return <OutcomeBadge outcome="RESOLVED" label="VALID" />;
    return <OutcomeBadge outcome="ENV_ERROR" label={check.outcome === 'UNRESOLVED' ? 'INVALID' : check.outcome.replace(/_/g, ' ')} />;
};

const DiffView: React.FC<{ patch: string }> = ({ patch }) => (
    <div className="p-4 rounded-xl font-mono text-xs leading-relaxed bg-slate-950 border border-slate-800 shadow-inner select-text max-h-[450px] overflow-auto">
        {patch.split('\n').map((line, idx) => {
            let colorClass = 'text-slate-300';
            let bgClass = '';
            if (line.startsWith('+') && !line.startsWith('+++')) {
                colorClass = 'text-emerald-400';
                bgClass = 'bg-emerald-500/10';
            } else if (line.startsWith('-') && !line.startsWith('---')) {
                colorClass = 'text-rose-400';
                bgClass = 'bg-rose-500/10';
            } else if (line.startsWith('@@')) {
                colorClass = 'text-cyan-400 font-semibold';
                bgClass = 'bg-cyan-500/10';
            } else if (line.startsWith('diff --git') || line.startsWith('index ')) {
                colorClass = 'text-indigo-400 font-bold';
            }
            return (
                <div key={idx} className={cn('px-3 py-0.5 rounded-sm whitespace-pre', colorClass, bgClass)}>
                    {line || ' '}
                </div>
            );
        })}
    </div>
);

const TestList: React.FC<{ title: string; tests: string[]; failed?: string[]; tone: 'rose' | 'blue' }> = ({ title, tests, failed, tone }) => (
    <div className="space-y-2">
        <h4 className={cn('text-xs font-bold uppercase tracking-wider flex items-center gap-2', tone === 'rose' ? 'text-rose-400' : 'text-blue-400')}>
            {tone === 'rose' ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />} {title} ({tests.length})
        </h4>
        <div className="space-y-1 max-h-64 overflow-y-auto">
            {tests.length === 0 && <div className="p-2.5 text-xs text-slate-500 font-mono">None</div>}
            {tests.map((name) => {
                const isFailed = failed?.includes(name);
                return (
                    <div key={name} className={cn(
                        'px-2.5 py-1.5 rounded-lg border font-mono text-xs flex items-center justify-between gap-3',
                        isFailed ? 'bg-rose-950/30 border-rose-900/50 text-rose-300' : 'bg-slate-950 border-slate-800 text-slate-400'
                    )}>
                        <span className="break-all">{name}</span>
                        {isFailed && <span className="text-[10px] font-semibold text-rose-400 shrink-0">FAILED</span>}
                    </div>
                );
            })}
        </div>
    </div>
);

interface TaskInspectionModalProps {
    task: BenchmarkTask | null;
    initialTab?: Tab;
    onClose: () => void;
    onRun: (instanceId: string) => void;
    canRun: boolean;
}

const TaskInspectionModal: React.FC<TaskInspectionModalProps> = ({ task, initialTab = 'description', onClose, onRun, canRun }) => {
    const [activeTab, setActiveTab] = useState<Tab>(initialTab);
    const [showGold, setShowGold] = useState(false);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        setActiveTab(initialTab);
        setShowGold(false);
    }, [initialTab, task?.instance_id]);

    if (!task) return null;

    const result = task.latest_result;
    const analysis = result?.analysis;
    const shownPatch = showGold ? task.patch : result?.patch || '';

    const handleCopy = () => {
        navigator.clipboard.writeText(shownPatch);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
        { id: 'description', label: 'Problem Statement', icon: <FileText size={15} /> },
        { id: 'result', label: 'Latest Result', icon: <ListChecks size={15} /> },
        { id: 'tests', label: 'Grading Tests', icon: <CheckSquare size={15} /> },
        { id: 'patch', label: 'Patch', icon: <Code size={15} /> },
    ];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-200">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-5xl w-full max-h-[90vh] flex flex-col shadow-2xl overflow-hidden">
                <div className="px-6 py-4 border-b border-slate-800 bg-slate-800/40 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                            <FileCode size={22} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2.5 flex-wrap">
                                <h3 className="text-white font-bold text-lg tracking-tight font-mono">{task.instance_id}</h3>
                                <OutcomeBadge outcome={result?.outcome} />
                                <EnvBadge check={task.environment_check} />
                            </div>
                            <div className="flex items-center gap-2 text-xs text-slate-400 font-mono mt-1 flex-wrap">
                                <span className="flex items-center gap-1 text-slate-300">
                                    <GitBranch size={13} className="text-slate-400" /> {task.repo} {task.version}
                                </span>
                                <span>·</span>
                                <span>base {task.base_commit.substring(0, 10)}</span>
                                {result && (
                                    <>
                                        <span>·</span>
                                        <span className="flex items-center gap-1 text-indigo-300">
                                            <Clock size={12} /> {result.runtime_seconds}s
                                        </span>
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-colors" title="Close">
                        <X size={20} />
                    </button>
                </div>

                <div className="flex items-center gap-1 px-6 bg-slate-950/70 border-b border-slate-800 text-xs font-medium overflow-x-auto">
                    {tabs.map((t) => (
                        <button
                            key={t.id}
                            onClick={() => setActiveTab(t.id)}
                            className={cn(
                                'flex items-center gap-2 py-3 px-4 border-b-2 transition-colors whitespace-nowrap',
                                activeTab === t.id ? 'border-indigo-500 text-indigo-400 font-semibold' : 'border-transparent text-slate-400 hover:text-slate-200'
                            )}
                        >
                            {t.icon}
                            {t.label}
                        </button>
                    ))}
                </div>

                <div className="p-6 overflow-y-auto max-h-[60vh] bg-slate-900/50 flex-1 space-y-6">
                    {activeTab === 'description' && (
                        <div className="space-y-2">
                            <p className="text-xs text-slate-500">
                                This is the only task input the agent receives, together with the repository at the base commit.
                            </p>
                            <div className="bg-slate-950 p-5 rounded-xl border border-slate-800 font-mono text-xs text-slate-300 leading-relaxed whitespace-pre-wrap select-text shadow-inner">
                                {task.problem_statement}
                            </div>
                        </div>
                    )}

                    {activeTab === 'result' && (
                        <div className="space-y-5">
                            {!result ? (
                                <div className="p-5 rounded-xl bg-slate-800/30 border border-slate-800 text-center space-y-2">
                                    <Cpu size={28} className="text-slate-500 mx-auto" />
                                    <h4 className="text-sm font-semibold text-slate-300">Not evaluated yet</h4>
                                    <p className="text-xs text-slate-400">Run the agent on this task to see its analysis, patch and test results.</p>
                                </div>
                            ) : (
                                <>
                                    <div className="p-4 rounded-xl bg-slate-800/40 border border-slate-700/60 space-y-2 text-xs">
                                        <div className="flex items-center gap-2">
                                            <OutcomeBadge outcome={result.outcome} />
                                            <span className="text-slate-400">{OUTCOME_HELP[result.outcome]}</span>
                                        </div>
                                        <p className="text-slate-200 font-mono whitespace-pre-wrap break-words">{result.detail}</p>
                                        <p className="text-slate-500 font-mono">
                                            {result.provider}/{result.model} · run {result.run_id} · {result.completed_at}
                                            {result.artifacts && <> · artifacts: eval_results/{result.artifacts}.*</>}
                                        </p>
                                    </div>

                                    {result.tests && (
                                        <div className="grid grid-cols-2 gap-4 text-xs">
                                            <div className="p-4 rounded-xl bg-slate-950 border border-slate-800">
                                                <span className="text-slate-400 block">FAIL_TO_PASS passing</span>
                                                <span className="text-2xl font-bold text-white">{result.tests.f2p_passed}/{result.tests.f2p_total}</span>
                                            </div>
                                            <div className="p-4 rounded-xl bg-slate-950 border border-slate-800">
                                                <span className="text-slate-400 block">PASS_TO_PASS passing</span>
                                                <span className="text-2xl font-bold text-white">{result.tests.p2p_passed}/{result.tests.p2p_total}</span>
                                            </div>
                                        </div>
                                    )}

                                    {analysis && (
                                        <div className="p-4 rounded-xl bg-indigo-950/30 border border-indigo-900/50 space-y-2 text-xs">
                                            <h4 className="font-bold text-indigo-400 uppercase tracking-wider">Agent issue analysis</h4>
                                            <p className="text-sm text-slate-200">{analysis.problem_summary}</p>
                                            {analysis.affected_areas && analysis.affected_areas.length > 0 && (
                                                <div className="flex flex-wrap gap-1.5">
                                                    {analysis.affected_areas.map((a) => (
                                                        <span key={a} className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700 font-mono">{a}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {result.test_log_tail && (
                                        <div className="space-y-2">
                                            <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">Test log (tail)</h4>
                                            <pre className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-[11px] text-slate-400 max-h-72 overflow-auto whitespace-pre-wrap">
                                                {result.test_log_tail}
                                            </pre>
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    )}

                    {activeTab === 'tests' && (
                        <div className="space-y-5">
                            <p className="text-xs text-slate-500">
                                Graded with the maintainers' test patch, which the agent never sees. FAIL_TO_PASS tests must pass after the fix;
                                PASS_TO_PASS tests must keep passing.
                            </p>
                            <TestList title="Fail to pass" tests={task.FAIL_TO_PASS} failed={result?.tests?.f2p_failed} tone="rose" />
                            <TestList title="Pass to pass" tests={task.PASS_TO_PASS} failed={result?.tests?.p2p_failed} tone="blue" />
                        </div>
                    )}

                    {activeTab === 'patch' && (
                        <div className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs">
                                    <button onClick={() => setShowGold(false)} className={cn('px-3 py-1 rounded-md', !showGold ? 'bg-slate-800 text-white' : 'text-slate-400')}>
                                        Agent patch
                                    </button>
                                    <button onClick={() => setShowGold(true)} className={cn('px-3 py-1 rounded-md', showGold ? 'bg-slate-800 text-white' : 'text-slate-400')}>
                                        Reference fix
                                    </button>
                                </div>
                                {shownPatch && (
                                    <button
                                        onClick={handleCopy}
                                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors border border-slate-700"
                                    >
                                        {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                                        {copied ? 'Copied' : 'Copy'}
                                    </button>
                                )}
                            </div>
                            {shownPatch ? (
                                <DiffView patch={shownPatch} />
                            ) : (
                                <div className="p-10 rounded-xl bg-slate-800/30 border border-slate-800 text-center text-xs text-slate-400">
                                    No agent patch recorded for this task.
                                </div>
                            )}
                        </div>
                    )}
                </div>

                <div className="px-6 py-4 border-t border-slate-800 bg-slate-800/40 flex items-center justify-end gap-3">
                    <button onClick={onClose} className="px-4 py-2 rounded-xl text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors border border-slate-700">
                        Close
                    </button>
                    <button
                        onClick={() => onRun(task.instance_id)}
                        disabled={!canRun}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white transition-all shadow-md bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed"
                    >
                        <Play size={14} />
                        <span>Run agent on this task</span>
                    </button>
                </div>
            </div>
        </div>
    );
};

const POLL_MS = 5000;

export const Evaluation: React.FC = () => {
    const [tasks, setTasks] = useState<BenchmarkTask[]>([]);
    const [stats, setStats] = useState<EvaluationStats | null>(null);
    const [job, setJob] = useState<EvaluationJobStatus>({ running: false });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [allowNetwork, setAllowNetwork] = useState(false);
    const [selectedFilter, setSelectedFilter] = useState<Filter>('ALL');
    const [searchQuery, setSearchQuery] = useState('');
    const [inspectedId, setInspectedId] = useState<string | null>(null);
    const [modalInitialTab, setModalInitialTab] = useState<Tab>('description');

    const loadData = useCallback(async () => {
        try {
            const [statsData, tasksData, jobData] = await Promise.all([
                getEvaluationStats(),
                getBenchmarkTasks(),
                getEvaluationStatus()
            ]);
            setStats(statsData);
            setTasks(tasksData);
            setJob(jobData);
            setError(null);
        } catch (err) {
            setError(errorMessage(err, 'Failed to load SWE-bench benchmark data'));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadData();
    }, [loadData]);

    // While a run is in progress, refresh so results appear as each task finishes.
    useEffect(() => {
        if (!job.running) return;
        const timer = setInterval(loadData, POLL_MS);
        return () => clearInterval(timer);
    }, [job.running, loadData]);

    const startRun = async (taskIds: string[] | undefined, mode: 'agent' | 'gold') => {
        setError(null);
        try {
            setJob(await triggerEvaluationRun(taskIds, mode, allowNetwork));
        } catch (err) {
            setError(errorMessage(err, 'Could not start the evaluation run'));
        }
    };

    const stopRun = async () => {
        try {
            setJob(await stopEvaluationRun());
        } catch (err) {
            setError(errorMessage(err, 'Could not stop the evaluation run'));
        }
    };

    const outcomeOf = (t: BenchmarkTask) => t.latest_result?.outcome;
    const matchesFilter = (t: BenchmarkTask) => {
        const o = outcomeOf(t);
        switch (selectedFilter) {
            case 'RESOLVED': return o === 'RESOLVED';
            case 'AGENT_FAILED': return !!o && AGENT_FAILURES.includes(o);
            case 'INFRA': return !!o && INFRA_FAILURES.includes(o);
            case 'NOT_RUN': return !o;
            default: return true;
        }
    };
    const query = searchQuery.toLowerCase();
    const filteredTasks = tasks.filter((t) =>
        matchesFilter(t) &&
        (t.instance_id.toLowerCase().includes(query) ||
            t.repo.toLowerCase().includes(query) ||
            t.problem_statement.toLowerCase().includes(query))
    );
    const count = (pred: (o: EvaluationOutcome | undefined) => boolean) => tasks.filter((t) => pred(outcomeOf(t))).length;
    const filters: { id: Filter; label: string; n: number }[] = [
        { id: 'ALL', label: 'All', n: tasks.length },
        { id: 'RESOLVED', label: 'Resolved', n: count((o) => o === 'RESOLVED') },
        { id: 'AGENT_FAILED', label: 'Agent failed', n: count((o) => !!o && AGENT_FAILURES.includes(o)) },
        { id: 'INFRA', label: 'Infrastructure error', n: count((o) => !!o && INFRA_FAILURES.includes(o)) },
        { id: 'NOT_RUN', label: 'Not run', n: count((o) => !o) },
    ];
    const inspectedTask = tasks.find((t) => t.instance_id === inspectedId) || null;
    const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v}%`);

    if (loading) {
        return (
            <div className="p-8 max-w-7xl mx-auto space-y-8 animate-pulse text-slate-400">
                <div className="h-10 bg-slate-800/60 rounded-lg w-1/3"></div>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                    {[0, 1, 2, 3].map((i) => <div key={i} className="h-28 bg-slate-800/40 rounded-xl"></div>)}
                </div>
            </div>
        );
    }

    return (
        <div className="p-8 max-w-7xl mx-auto space-y-8">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <div className="flex items-center gap-3">
                        <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
                            <Award className="text-amber-400" size={28} /> SWE-bench Evaluation
                        </h2>
                        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                            {stats?.dataset} · {tasks.length} tasks
                        </span>
                    </div>
                    <p className="text-slate-400 text-sm mt-1">
                        Real SWE-bench Lite instances, graded with the maintainers' hidden tests in Docker.
                        Infrastructure failures are reported separately and never counted against the agent.
                    </p>
                </div>

                <div className="flex items-center gap-3 flex-wrap">
                    <label className="flex items-center gap-2 text-xs text-slate-400" title="psf/requests tests call httpbin.org; without network they are reported as ENV ERROR">
                        <input type="checkbox" checked={allowNetwork} onChange={(e) => setAllowNetwork(e.target.checked)} className="accent-indigo-500" />
                        Allow network
                    </label>
                    <button onClick={loadData} className="p-2.5 rounded-lg bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700 transition-colors border border-slate-700" title="Refresh">
                        <RotateCcw size={16} />
                    </button>
                    <button
                        onClick={() => startRun(undefined, 'gold')}
                        disabled={job.running}
                        className="flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors disabled:opacity-50"
                        title="Grade the reference fixes to confirm each environment can evaluate its task"
                    >
                        <ShieldCheck size={16} />
                        <span>Validate environments</span>
                    </button>
                    <button
                        onClick={() => startRun(undefined, 'agent')}
                        disabled={job.running}
                        className="flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium text-white transition-all shadow-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed"
                    >
                        <Play size={16} />
                        <span>Run agent on all</span>
                    </button>
                </div>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-900/60 text-rose-300 text-sm flex items-center gap-3">
                    <AlertCircle size={18} className="text-rose-400 shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            {job.running ? (
                <div className="p-4 rounded-xl bg-blue-950/40 border border-blue-900/60 text-blue-200 text-sm flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <RotateCcw size={18} className="animate-spin text-blue-400 shrink-0" />
                        <span>
                            {job.mode === 'gold' ? 'Validating environments' : 'Running agent'}: {job.done}/{job.total} done
                            {job.current && <> · now <span className="font-mono">{job.current}</span></>}
                            {job.skipped && job.skipped.length > 0 && <> · {job.skipped.length} skipped (need network)</>}
                        </span>
                    </div>
                    <button onClick={stopRun} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-slate-800 hover:bg-slate-700 border border-slate-700">
                        <Square size={12} /> Stop after current task
                    </button>
                </div>
            ) : (job.stopped_reason || job.error) && (
                <div className="p-4 rounded-xl bg-amber-950/40 border border-amber-900/60 text-amber-200 text-sm flex items-center gap-3">
                    <AlertCircle size={18} className="text-amber-400 shrink-0" />
                    <span>Last run {job.run_id} ended early: {job.error || job.stopped_reason}</span>
                </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60">
                    <span className="text-slate-400 font-medium text-sm flex items-center gap-2">
                        <CheckCircle2 size={16} className="text-emerald-400" /> Resolved
                    </span>
                    <div className="mt-4 flex items-baseline gap-2">
                        <span className="text-3xl font-bold text-white tracking-tight">{stats?.resolved ?? 0}/{stats?.total_tasks ?? 0}</span>
                        <span className="text-xs text-slate-500">{pct(stats?.resolution_rate_total)} of all tasks</span>
                    </div>
                </div>
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60">
                    <span className="text-slate-400 font-medium text-sm flex items-center gap-2" title="Resolved among tasks the agent actually attempted: excludes LLM/environment failures and tasks whose reference fix fails in this environment">
                        <ListChecks size={16} className="text-indigo-400" /> Resolved of attempted
                    </span>
                    <div className="mt-4 flex items-baseline gap-2">
                        <span className="text-3xl font-bold text-white tracking-tight">{pct(stats?.resolution_rate_attempted)}</span>
                        <span className="text-xs text-slate-500">{stats?.attempted ?? 0} attempted · {stats?.evaluated ?? 0} evaluated</span>
                    </div>
                </div>
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60">
                    <span className="text-slate-400 font-medium text-sm flex items-center gap-2" title="Tasks whose reference fix passes in this harness's environment">
                        <ShieldCheck size={16} className="text-amber-400" /> Environments validated
                    </span>
                    <div className="mt-4 flex items-baseline gap-2">
                        <span className="text-3xl font-bold text-white tracking-tight">{stats?.environment_validated ?? 0}/{stats?.total_tasks ?? 0}</span>
                        <span className="text-xs text-slate-500">
                            {stats?.environment_checked ?? 0} checked
                            {stats && stats.environment_invalid.length > 0 && ` · ${stats.environment_invalid.length} invalid`}
                        </span>
                    </div>
                </div>
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60">
                    <span className="text-slate-400 font-medium text-sm flex items-center gap-2">
                        <Cpu size={16} className="text-cyan-400" /> Last model
                    </span>
                    <div className="mt-4">
                        <span className="text-lg font-bold text-white tracking-tight truncate block">{stats?.last_model || '—'}</span>
                        <span className="text-xs text-slate-500">
                            {stats?.last_provider || 'no runs yet'}
                            {stats?.average_runtime_seconds ? ` · avg ${stats.average_runtime_seconds}s per task` : ''}
                        </span>
                    </div>
                </div>
            </div>

            {stats && Object.keys(stats.outcomes).length > 0 && (
                <div className="flex flex-wrap gap-2 text-xs">
                    {(Object.entries(stats.outcomes) as [EvaluationOutcome, number][]).map(([o, n]) => (
                        <span key={o} className="flex items-center gap-1.5">
                            <OutcomeBadge outcome={o} /> <span className="text-slate-400">{n}</span>
                        </span>
                    ))}
                </div>
            )}

            <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
                <div className="flex items-center gap-1.5 bg-slate-900 p-1 rounded-lg border border-slate-800 w-full sm:w-auto overflow-x-auto">
                    {filters.map((f) => (
                        <button
                            key={f.id}
                            onClick={() => setSelectedFilter(f.id)}
                            className={cn(
                                'px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap',
                                selectedFilter === f.id ? 'bg-slate-800 text-white shadow-sm' : 'text-slate-400 hover:text-white'
                            )}
                        >
                            {f.label} ({f.n})
                        </button>
                    ))}
                </div>
                <div className="relative w-full sm:w-80">
                    <Search className="absolute left-3 top-2.5 text-slate-500" size={16} />
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search tasks, repos, issue text..."
                        className="w-full pl-9 pr-4 py-2 bg-slate-900 border border-slate-800 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                </div>
            </div>

            <div className="border border-slate-800 rounded-xl overflow-x-auto shadow-sm bg-slate-900">
                <table className="w-full text-left text-sm text-slate-400">
                    <thead className="text-xs uppercase bg-slate-800/80 text-slate-300">
                        <tr>
                            <th className="px-6 py-4 font-semibold">Instance</th>
                            <th className="px-6 py-4 font-semibold">Repository</th>
                            <th className="px-6 py-4 font-semibold">Issue</th>
                            <th className="px-6 py-4 font-semibold">Environment</th>
                            <th className="px-6 py-4 font-semibold">Agent result</th>
                            <th className="px-6 py-4 font-semibold">Runtime</th>
                            <th className="px-6 py-4 font-semibold text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50">
                        {filteredTasks.length === 0 ? (
                            <tr>
                                <td colSpan={7} className="px-6 py-12 text-center text-slate-500">No tasks match.</td>
                            </tr>
                        ) : (
                            filteredTasks.map((t) => {
                                const isCurrent = job.running && job.current === t.instance_id;
                                return (
                                    <tr
                                        key={t.instance_id}
                                        onClick={() => { setInspectedId(t.instance_id); setModalInitialTab('description'); }}
                                        className="hover:bg-slate-800/40 transition-colors group cursor-pointer"
                                    >
                                        <td className="px-6 py-4 font-mono font-medium text-white group-hover:text-indigo-400 whitespace-nowrap">{t.instance_id}</td>
                                        <td className="px-6 py-4 whitespace-nowrap">
                                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-800/60 border border-slate-700/50 text-slate-300 text-xs">
                                                <GitBranch size={12} className="text-slate-400" /> {t.repo} {t.version}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4 max-w-xs truncate text-slate-300" title={t.problem_statement}>
                                            {t.problem_statement.split('\n')[0]}
                                        </td>
                                        <td className="px-6 py-4"><EnvBadge check={t.environment_check} /></td>
                                        <td className="px-6 py-4">
                                            {isCurrent ? (
                                                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400 border border-blue-500/30 animate-pulse">
                                                    <RotateCcw size={12} className="animate-spin" /> Running
                                                </span>
                                            ) : (
                                                <OutcomeBadge outcome={t.latest_result?.outcome} />
                                            )}
                                        </td>
                                        <td className="px-6 py-4 font-mono text-xs whitespace-nowrap">
                                            {t.latest_result ? `${t.latest_result.runtime_seconds}s` : '—'}
                                        </td>
                                        <td className="px-6 py-4 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                                            <div className="flex items-center justify-end gap-2">
                                                <button
                                                    onClick={() => { setInspectedId(t.instance_id); setModalInitialTab('result'); }}
                                                    className="px-2.5 py-1 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors border border-slate-700 flex items-center gap-1"
                                                >
                                                    <Eye size={13} /> Details
                                                </button>
                                                <button
                                                    onClick={() => startRun([t.instance_id], 'agent')}
                                                    disabled={job.running}
                                                    className="px-2.5 py-1 text-xs font-medium rounded bg-indigo-600/80 hover:bg-indigo-600 text-white transition-colors disabled:opacity-40 flex items-center gap-1"
                                                >
                                                    <Play size={12} /> Run
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })
                        )}
                    </tbody>
                </table>
            </div>

            <TaskInspectionModal
                task={inspectedTask}
                initialTab={modalInitialTab}
                onClose={() => setInspectedId(null)}
                onRun={(id) => startRun([id], 'agent')}
                canRun={!job.running}
            />
        </div>
    );
};

export default Evaluation;
