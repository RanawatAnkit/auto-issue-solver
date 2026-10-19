import React, { useEffect, useState } from 'react';
import { 
    getBenchmarkTasks, 
    getEvaluationStats, 
    triggerEvaluationRun, 
    errorMessage 
} from '../services/api';
import { BenchmarkTask, EvaluationStats } from '../types/domain';
import { StatusBadge } from '../components/layout/StatusBadge';
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
    Sparkles,
    Code
} from 'lucide-react';
import { cn } from '../utils/cn';

interface TaskInspectionModalProps {
    task: BenchmarkTask | null;
    initialTab?: 'description' | 'analysis' | 'tests' | 'patch';
    onClose: () => void;
    onRun: (instanceId: string) => void;
    isRunning: boolean;
}

const TaskInspectionModal: React.FC<TaskInspectionModalProps> = ({ 
    task, 
    initialTab = 'description', 
    onClose, 
    onRun, 
    isRunning 
}) => {
    const [activeTab, setActiveTab] = useState<'description' | 'analysis' | 'tests' | 'patch'>(initialTab);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        setActiveTab(initialTab);
    }, [initialTab, task?.instance_id]);

    if (!task) return null;

    const handleCopy = () => {
        navigator.clipboard.writeText(task.latest_patch || '');
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const diffLines = task.latest_patch ? task.latest_patch.split('\n') : [];
    const analysis = task.latest_analysis;
    const refSolution = task.reference_solution;
    const testSuite = task.test_suite;

    const difficultyColors: Record<string, string> = {
        Easy: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
        Medium: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
        Hard: 'text-rose-400 bg-rose-500/10 border-rose-500/30',
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-200">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-5xl w-full max-h-[90vh] flex flex-col shadow-2xl overflow-hidden">
                {/* Modal Header */}
                <div className="px-6 py-4 border-b border-slate-800 bg-slate-800/40 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                            <FileCode size={22} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2.5 flex-wrap">
                                <h3 className="text-white font-bold text-lg tracking-tight font-mono">
                                    {task.instance_id}
                                </h3>
                                <StatusBadge status={task.latest_status || 'PENDING'} />
                                {task.difficulty && (
                                    <span className={cn('px-2.5 py-0.5 rounded-full text-xs font-semibold border', difficultyColors[task.difficulty] || 'text-slate-300')}>
                                        {task.difficulty}
                                    </span>
                                )}
                                {task.category && (
                                    <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700">
                                        {task.category}
                                    </span>
                                )}
                            </div>
                            <div className="flex items-center gap-2 text-xs text-slate-400 font-mono mt-1">
                                <span className="flex items-center gap-1 text-slate-300">
                                    <GitBranch size={13} className="text-slate-400" /> {task.repo}
                                </span>
                                <span>·</span>
                                <span>Base Commit: {task.base_commit.substring(0, 8)}</span>
                                {task.latest_runtime && (
                                    <>
                                        <span>·</span>
                                        <span className="flex items-center gap-1 text-indigo-300">
                                            <Clock size={12} /> {task.latest_runtime}s
                                        </span>
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
                        title="Close Modal"
                    >
                        <X size={20} />
                    </button>
                </div>

                {/* Navigation Tabs Bar */}
                <div className="flex items-center gap-1 px-6 bg-slate-950/70 border-b border-slate-800 text-xs font-medium">
                    <button
                        onClick={() => setActiveTab('description')}
                        className={cn(
                            "flex items-center gap-2 py-3 px-4 border-b-2 transition-colors",
                            activeTab === 'description' 
                                ? "border-indigo-500 text-indigo-400 font-semibold" 
                                : "border-transparent text-slate-400 hover:text-slate-200"
                        )}
                    >
                        <FileText size={15} />
                        Problem Description
                    </button>
                    <button
                        onClick={() => setActiveTab('analysis')}
                        className={cn(
                            "flex items-center gap-2 py-3 px-4 border-b-2 transition-colors",
                            activeTab === 'analysis' 
                                ? "border-indigo-500 text-indigo-400 font-semibold" 
                                : "border-transparent text-slate-400 hover:text-slate-200"
                        )}
                    >
                        <ListChecks size={15} />
                        Issue Analysis & Strategy
                        {analysis && <span className="w-1.5 h-1.5 rounded-full bg-indigo-400"></span>}
                    </button>
                    <button
                        onClick={() => setActiveTab('tests')}
                        className={cn(
                            "flex items-center gap-2 py-3 px-4 border-b-2 transition-colors",
                            activeTab === 'tests' 
                                ? "border-indigo-500 text-indigo-400 font-semibold" 
                                : "border-transparent text-slate-400 hover:text-slate-200"
                        )}
                    >
                        <CheckSquare size={15} />
                        Test Suite & Verification
                    </button>
                    <button
                        onClick={() => setActiveTab('patch')}
                        className={cn(
                            "flex items-center gap-2 py-3 px-4 border-b-2 transition-colors",
                            activeTab === 'patch' 
                                ? "border-indigo-500 text-indigo-400 font-semibold" 
                                : "border-transparent text-slate-400 hover:text-slate-200"
                        )}
                    >
                        <Code size={15} />
                        Patch Diff
                        {task.latest_patch && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>}
                    </button>
                </div>

                {/* Modal Body with Tab Views */}
                <div className="p-6 overflow-y-auto max-h-[60vh] bg-slate-900/50 flex-1 space-y-6">
                    {/* TAB 1: DESCRIPTION */}
                    {activeTab === 'description' && (
                        <div className="space-y-6">
                            {/* Target Files & Info Box */}
                            <div className="p-4 rounded-xl bg-slate-800/40 border border-slate-700/60 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
                                <div>
                                    <span className="text-slate-400 block mb-1 font-medium">Target Files to Modify:</span>
                                    <div className="flex flex-wrap gap-1.5">
                                        {task.files_to_modify.map((f, i) => (
                                            <span key={i} className="font-mono bg-slate-900 text-indigo-300 px-2.5 py-1 rounded border border-slate-700">
                                                {f}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                                <div className="text-right sm:border-l sm:border-slate-700/60 sm:pl-4">
                                    <span className="text-slate-400 block mb-1 font-medium">Benchmark Dataset:</span>
                                    <span className="text-slate-200 font-semibold">SWE-bench Lite Curated</span>
                                </div>
                            </div>

                            {/* Full Problem Statement */}
                            <div className="space-y-2">
                                <h4 className="text-sm font-semibold text-white flex items-center gap-2">
                                    <FileText size={16} className="text-indigo-400" /> Full Problem Statement
                                </h4>
                                <div className="bg-slate-950 p-5 rounded-xl border border-slate-800 font-mono text-xs text-slate-300 leading-relaxed whitespace-pre-wrap select-text shadow-inner">
                                    {task.problem_statement}
                                </div>
                            </div>

                            {/* Hints or reproduction context if provided */}
                            {task.hints_text && (
                                <div className="space-y-2">
                                    <h4 className="text-sm font-semibold text-white flex items-center gap-2">
                                        <Sparkles size={16} className="text-amber-400" /> Developer Hints & Context
                                    </h4>
                                    <div className="bg-slate-950/80 p-4 rounded-xl border border-slate-800/80 text-xs text-slate-400 italic">
                                        {task.hints_text}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {/* TAB 2: ANALYSIS & STRATEGY */}
                    {activeTab === 'analysis' && (
                        <div className="space-y-6">
                            {/* Live AI Analysis from previous run */}
                            {analysis ? (
                                <div className="space-y-4">
                                    <div className="p-4 rounded-xl bg-indigo-950/30 border border-indigo-900/50">
                                        <h4 className="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-1 flex items-center gap-2">
                                            <Sparkles size={14} /> AI Issue Summary & Root Cause
                                        </h4>
                                        <p className="text-sm text-slate-200 leading-relaxed">
                                            {analysis.problem_summary || 'Analysis completed.'}
                                        </p>
                                    </div>

                                    {/* Expected vs Current Behavior Grid */}
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div className="p-4 rounded-xl bg-emerald-950/20 border border-emerald-900/40 space-y-1">
                                            <span className="text-xs font-bold text-emerald-400 uppercase tracking-wider block">
                                                Expected Behavior
                                            </span>
                                            <p className="text-xs text-slate-300 leading-relaxed">
                                                {analysis.expected_behavior || 'System behaves in accordance with specification.'}
                                            </p>
                                        </div>
                                        <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-900/40 space-y-1">
                                            <span className="text-xs font-bold text-rose-400 uppercase tracking-wider block">
                                                Current Failure / Anomaly
                                            </span>
                                            <p className="text-xs text-slate-300 leading-relaxed">
                                                {analysis.current_behavior || 'Unexpected exception or failure raised.'}
                                            </p>
                                        </div>
                                    </div>

                                    {/* Affected Areas & Constraints */}
                                    {analysis.affected_areas && analysis.affected_areas.length > 0 && (
                                        <div className="space-y-2">
                                            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block">
                                                Affected Components
                                            </span>
                                            <div className="flex flex-wrap gap-2">
                                                {analysis.affected_areas.map((area, idx) => (
                                                    <span key={idx} className="px-2.5 py-1 rounded bg-slate-800 text-slate-300 text-xs border border-slate-700">
                                                        {area}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {/* Acceptance Criteria */}
                                    {analysis.acceptance_criteria && analysis.acceptance_criteria.length > 0 && (
                                        <div className="space-y-2">
                                            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block">
                                                Acceptance Criteria
                                            </span>
                                            <ul className="space-y-1.5 text-xs text-slate-300">
                                                {analysis.acceptance_criteria.map((item, idx) => (
                                                    <li key={idx} className="flex items-start gap-2">
                                                        <CheckCircle2 size={14} className="text-emerald-400 shrink-0 mt-0.5" />
                                                        <span>{item}</span>
                                                    </li>
                                                ))}
                                            </ul>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="p-5 rounded-xl bg-slate-800/30 border border-slate-800 text-center space-y-2">
                                    <Cpu size={28} className="text-slate-500 mx-auto" />
                                    <h4 className="text-sm font-semibold text-slate-300">No Live AI Analysis Recorded Yet</h4>
                                    <p className="text-xs text-slate-400 max-w-md mx-auto">
                                        Click <strong>Run Evaluation</strong> to have the agent analyze the root cause and synthesize a patch for this task.
                                    </p>
                                </div>
                            )}

                            {/* Reference Solution Strategy from Benchmark Dataset */}
                            {refSolution && (
                                <div className="p-4 rounded-xl bg-slate-800/40 border border-slate-700/60 space-y-3">
                                    <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                                        <Code size={14} className="text-indigo-400" /> Reference Benchmark Solution
                                    </h4>
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                                        <div>
                                            <span className="text-slate-400 block mb-0.5">Target Function / Class:</span>
                                            <span className="font-mono text-indigo-300 font-semibold bg-slate-900 px-2 py-0.5 rounded border border-slate-700 inline-block">
                                                {refSolution.target_function || 'solve'}
                                            </span>
                                        </div>
                                        <div>
                                            <span className="text-slate-400 block mb-0.5">File Path:</span>
                                            <span className="font-mono text-slate-200">
                                                {refSolution.file_path || task.files_to_modify[0]}
                                            </span>
                                        </div>
                                    </div>
                                    <div>
                                        <span className="text-slate-400 text-xs block mb-0.5">Recommended Strategy:</span>
                                        <p className="text-xs text-slate-300 bg-slate-900/80 p-3 rounded-lg border border-slate-800">
                                            {refSolution.summary}
                                        </p>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {/* TAB 3: TEST SUITE & VERIFICATION */}
                    {activeTab === 'tests' && (
                        <div className="space-y-6">
                            {testSuite ? (
                                <div className="space-y-4">
                                    <div className="p-4 rounded-xl bg-slate-800/40 border border-slate-700/60 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
                                        <div>
                                            <span className="text-slate-400 block mb-1">Target Test Suite:</span>
                                            <span className="font-mono text-white font-semibold text-sm">
                                                {testSuite.test_file || 'tests/test_benchmark.py'}
                                            </span>
                                        </div>
                                        {testSuite.test_name && (
                                            <div className="text-right">
                                                <span className="text-slate-400 block mb-1">Primary Test Function:</span>
                                                <span className="font-mono text-indigo-300 bg-slate-900 px-2.5 py-1 rounded border border-slate-700">
                                                    {testSuite.test_name}
                                                </span>
                                            </div>
                                        )}
                                    </div>

                                    {/* Fail-to-Pass Tests (Proof of Fix) */}
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <h4 className="text-xs font-bold text-rose-400 uppercase tracking-wider flex items-center gap-2">
                                                <AlertCircle size={14} /> Fail-To-Pass Tests (Proof of Bug Fix)
                                            </h4>
                                            <span className="text-xs text-slate-500">
                                                Must transition from FAIL &rarr; PASS
                                            </span>
                                        </div>
                                        <div className="space-y-1.5">
                                            {testSuite.fail_to_pass && testSuite.fail_to_pass.length > 0 ? (
                                                testSuite.fail_to_pass.map((tname, idx) => (
                                                    <div key={idx} className="p-2.5 rounded-lg bg-rose-950/20 border border-rose-900/40 font-mono text-xs text-rose-300 flex items-center justify-between">
                                                        <span>{tname}</span>
                                                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-rose-500/20 text-rose-400 border border-rose-500/30">
                                                            Pass Criteria
                                                        </span>
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="p-3 rounded-lg bg-slate-800/20 text-xs text-slate-500 font-mono">
                                                    No specific fail_to_pass tests defined.
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* Pass-to-Pass Tests (Regression Suite) */}
                                    <div className="space-y-2 pt-2">
                                        <div className="flex items-center justify-between">
                                            <h4 className="text-xs font-bold text-blue-400 uppercase tracking-wider flex items-center gap-2">
                                                <CheckCircle2 size={14} /> Pass-To-Pass Tests (Regression Prevention)
                                            </h4>
                                            <span className="text-xs text-slate-500">
                                                Must remain passing
                                            </span>
                                        </div>
                                        <div className="space-y-1.5">
                                            {testSuite.pass_to_pass && testSuite.pass_to_pass.length > 0 ? (
                                                testSuite.pass_to_pass.map((tname, idx) => (
                                                    <div key={idx} className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 font-mono text-xs text-slate-400 flex items-center justify-between">
                                                        <span>{tname}</span>
                                                        <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700">
                                                            Regression Check
                                                        </span>
                                                    </div>
                                                ))
                                            ) : (
                                                <div className="p-3 rounded-lg bg-slate-800/20 text-xs text-slate-500 font-mono">
                                                    General test suite passed.
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            ) : (
                                <div className="p-8 text-center text-slate-500 text-xs">
                                    No specific test suite mapping for this benchmark instance.
                                </div>
                            )}
                        </div>
                    )}

                    {/* TAB 4: CODE PATCH DIFF */}
                    {activeTab === 'patch' && (
                        <div className="space-y-4">
                            {task.latest_patch ? (
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <span className="text-xs text-slate-400 font-mono">
                                            Target: {task.files_to_modify?.[0] || 'solution.py'}
                                        </span>
                                        <button
                                            onClick={handleCopy}
                                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors border border-slate-700"
                                        >
                                            {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                                            {copied ? 'Copied to Clipboard!' : 'Copy Patch Diff'}
                                        </button>
                                    </div>
                                    <div className="p-4 rounded-xl font-mono text-xs leading-relaxed bg-slate-950 border border-slate-800 shadow-inner select-text max-h-[450px] overflow-y-auto">
                                        {diffLines.map((line, idx) => {
                                            let colorClass = 'text-slate-300';
                                            let bgClass = '';
                                            if (line.startsWith('+')) {
                                                colorClass = 'text-emerald-400';
                                                bgClass = 'bg-emerald-500/10';
                                            } else if (line.startsWith('-')) {
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
                                </div>
                            ) : (
                                <div className="p-10 rounded-xl bg-slate-800/30 border border-slate-800 text-center space-y-3">
                                    <Code size={32} className="text-slate-500 mx-auto" />
                                    <h4 className="text-sm font-semibold text-white">No Patch Diff Generated Yet</h4>
                                    <p className="text-xs text-slate-400 max-w-sm mx-auto">
                                        This task is currently pending. Click <strong>Run Evaluation</strong> below to generate a code patch with Gemini.
                                    </p>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Modal Footer Controls */}
                <div className="px-6 py-4 border-t border-slate-800 bg-slate-800/40 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        {task.latest_runtime ? (
                            <span className="text-xs font-mono text-slate-400 flex items-center gap-1.5">
                                <Clock size={14} className="text-indigo-400" />
                                Execution time: <strong className="text-white">{task.latest_runtime}s</strong>
                            </span>
                        ) : (
                            <span className="text-xs text-slate-500">
                                Status: Ready for evaluation
                            </span>
                        )}
                    </div>
                    <div className="flex items-center gap-3">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 rounded-xl text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors border border-slate-700"
                        >
                            Close
                        </button>
                        <button
                            onClick={() => onRun(task.instance_id)}
                            disabled={isRunning}
                            className={cn(
                                "flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white transition-all shadow-md",
                                isRunning 
                                    ? "bg-indigo-600/50 cursor-not-allowed" 
                                    : "bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] shadow-indigo-600/20"
                            )}
                        >
                            {isRunning ? (
                                <>
                                    <RotateCcw size={14} className="animate-spin" />
                                    <span>Evaluating with Gemini...</span>
                                </>
                            ) : (
                                <>
                                    <Play size={14} />
                                    <span>{task.latest_status === 'RESOLVED' ? 'Re-Run Evaluation' : 'Run Evaluation'}</span>
                                </>
                            )}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export const Evaluation: React.FC = () => {
    const [tasks, setTasks] = useState<BenchmarkTask[]>([]);
    const [stats, setStats] = useState<EvaluationStats | null>(null);
    const [loading, setLoading] = useState(true);
    const [evaluating, setEvaluating] = useState(false);
    const [evaluatingTaskId, setEvaluatingTaskId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [successBanner, setSuccessBanner] = useState<string | null>(null);
    const [selectedFilter, setSelectedFilter] = useState<'ALL' | 'RESOLVED' | 'FAILED' | 'PENDING'>('ALL');
    const [searchQuery, setSearchQuery] = useState('');
    const [inspectedTask, setInspectedTask] = useState<BenchmarkTask | null>(null);
    const [modalInitialTab, setModalInitialTab] = useState<'description' | 'analysis' | 'tests' | 'patch'>('description');

    const loadData = async () => {
        try {
            const [statsData, tasksData] = await Promise.all([
                getEvaluationStats(),
                getBenchmarkTasks()
            ]);
            setStats(statsData);
            setTasks(tasksData);
            // If currently viewing a task, update its state
            if (inspectedTask) {
                const updated = tasksData.find(t => t.instance_id === inspectedTask.instance_id);
                if (updated) setInspectedTask(updated);
            }
            setError(null);
        } catch (err) {
            setError(errorMessage(err, 'Failed to load SWE-bench benchmark data'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadData();
    }, []);

    const handleRunAll = async () => {
        setEvaluating(true);
        setEvaluatingTaskId('ALL');
        setError(null);
        setSuccessBanner(null);

        try {
            const res = await triggerEvaluationRun();
            setSuccessBanner(`Evaluation completed: ${res.resolved_count}/${res.total_evaluated} benchmark tasks resolved successfully.`);
            await loadData();
        } catch (err) {
            setError(errorMessage(err, 'Failed to complete benchmark run'));
        } finally {
            setEvaluating(false);
            setEvaluatingTaskId(null);
        }
    };

    const handleRunSingle = async (instanceId: string) => {
        setEvaluating(true);
        setEvaluatingTaskId(instanceId);
        setError(null);
        setSuccessBanner(null);

        try {
            const res = await triggerEvaluationRun([instanceId]);
            const taskResult = res.results?.[0];
            const statusText = taskResult?.status === 'RESOLVED' ? 'RESOLVED' : 'FAILED';
            setSuccessBanner(`Task ${instanceId} evaluated: ${statusText} in ${taskResult?.runtime_seconds || 0}s`);
            await loadData();
        } catch (err) {
            setError(errorMessage(err, `Failed to evaluate task ${instanceId}`));
        } finally {
            setEvaluating(false);
            setEvaluatingTaskId(null);
        }
    };

    const handleOpenTask = (task: BenchmarkTask, tab: 'description' | 'analysis' | 'tests' | 'patch' = 'description') => {
        setInspectedTask(task);
        setModalInitialTab(tab);
    };

    // Filter tasks
    const filteredTasks = tasks.filter((t) => {
        const matchesFilter = 
            selectedFilter === 'ALL' ||
            (selectedFilter === 'RESOLVED' && t.latest_status === 'RESOLVED') ||
            (selectedFilter === 'FAILED' && t.latest_status === 'FAILED') ||
            (selectedFilter === 'PENDING' && (t.latest_status === 'PENDING' || !t.latest_status));

        const matchesSearch = 
            t.instance_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
            t.repo.toLowerCase().includes(searchQuery.toLowerCase()) ||
            (t.category && t.category.toLowerCase().includes(searchQuery.toLowerCase())) ||
            (t.difficulty && t.difficulty.toLowerCase().includes(searchQuery.toLowerCase())) ||
            t.problem_statement.toLowerCase().includes(searchQuery.toLowerCase());

        return matchesFilter && matchesSearch;
    });

    const resolvedCount = tasks.filter((t) => t.latest_status === 'RESOLVED').length;
    const failedCount = tasks.filter((t) => t.latest_status === 'FAILED').length;
    const pendingCount = tasks.filter((t) => !t.latest_status || t.latest_status === 'PENDING').length;

    if (loading) {
        return (
            <div className="p-8 max-w-7xl mx-auto space-y-8 animate-pulse text-slate-400">
                <div className="h-10 bg-slate-800/60 rounded-lg w-1/3"></div>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                    <div className="h-28 bg-slate-800/40 rounded-xl"></div>
                    <div className="h-28 bg-slate-800/40 rounded-xl"></div>
                    <div className="h-28 bg-slate-800/40 rounded-xl"></div>
                    <div className="h-28 bg-slate-800/40 rounded-xl"></div>
                </div>
            </div>
        );
    }

    return (
        <div className="p-8 max-w-7xl mx-auto space-y-8">
            {/* Page Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <div className="flex items-center gap-3">
                        <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
                            <Award className="text-amber-400" size={28} /> SWE-bench Evaluation Suite
                        </h2>
                        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                            30 Curated Benchmark Tasks
                        </span>
                    </div>
                    <p className="text-slate-400 text-sm mt-1">
                        Empirical validation against real-world GitHub issues using isolated unit test execution. Click any issue to view descriptions, AI analysis, test suites, and patches.
                    </p>
                </div>

                <div className="flex items-center gap-3">
                    <button
                        onClick={loadData}
                        disabled={evaluating}
                        className="p-2.5 rounded-lg bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700 transition-colors border border-slate-700 disabled:opacity-50"
                        title="Refresh Benchmark Stats"
                    >
                        <RotateCcw size={16} />
                    </button>
                    <button
                        onClick={handleRunAll}
                        disabled={evaluating}
                        className={cn(
                            "flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium text-white transition-all shadow-lg",
                            evaluating 
                                ? "bg-indigo-600/50 cursor-not-allowed" 
                                : "bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] shadow-indigo-600/20"
                        )}
                    >
                        {evaluating && evaluatingTaskId === 'ALL' ? (
                            <>
                                <RotateCcw size={16} className="animate-spin" />
                                <span>Running Suite...</span>
                            </>
                        ) : (
                            <>
                                <Play size={16} />
                                <span>Run Full Suite</span>
                            </>
                        )}
                    </button>
                </div>
            </div>

            {/* Error & Success Alerts */}
            {error && (
                <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-900/60 text-rose-300 text-sm flex items-center gap-3">
                    <AlertCircle size={18} className="text-rose-400 shrink-0" />
                    <span>{error}</span>
                </div>
            )}
            {successBanner && (
                <div className="p-4 rounded-xl bg-emerald-950/40 border border-emerald-900/60 text-emerald-300 text-sm flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
                        <span>{successBanner}</span>
                    </div>
                    <button onClick={() => setSuccessBanner(null)} className="text-emerald-400 hover:text-emerald-200">
                        <X size={16} />
                    </button>
                </div>
            )}

            {/* Metric KPI Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                {/* Pass Rate */}
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60 flex flex-col justify-between">
                    <div className="flex items-center justify-between">
                        <span className="text-slate-400 font-medium text-sm flex items-center gap-2">
                            <CheckCircle2 size={16} className="text-emerald-400" /> Resolution Rate
                        </span>
                        <span className="text-xs font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            {stats?.resolved_count || 0}/{stats?.total_evaluated || 0} Solved
                        </span>
                    </div>
                    <div className="mt-4">
                        <div className="flex items-baseline gap-2">
                            <span className="text-3xl font-bold text-white tracking-tight">
                                {stats?.pass_rate_percentage ?? 0}%
                            </span>
                            <span className="text-xs text-slate-500">pass rate</span>
                        </div>
                        {/* Progress Bar */}
                        <div className="w-full bg-slate-700/40 rounded-full h-1.5 mt-3 overflow-hidden">
                            <div 
                                className="bg-emerald-400 h-1.5 rounded-full transition-all duration-500" 
                                style={{ width: `${stats?.pass_rate_percentage || 0}%` }}
                            />
                        </div>
                    </div>
                </div>

                {/* Total Benchmark Tasks */}
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60 flex flex-col justify-between">
                    <div className="flex items-center justify-between">
                        <span className="text-slate-400 font-medium text-sm flex items-center gap-2">
                            <Award size={16} className="text-amber-400" /> Curated Instances
                        </span>
                        <span className="text-xs font-mono text-slate-400">SWE-bench Lite</span>
                    </div>
                    <div className="mt-4">
                        <span className="text-3xl font-bold text-white tracking-tight">
                            {stats?.total_benchmark_tasks ?? tasks.length}
                        </span>
                        <p className="text-xs text-slate-400 mt-1">
                            {stats?.total_evaluated ?? 0} evaluated · {pendingCount} pending
                        </p>
                    </div>
                </div>

                {/* Mean Resolution Time */}
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60 flex flex-col justify-between">
                    <div className="flex items-center justify-between">
                        <span className="text-slate-400 font-medium text-sm flex items-center gap-2">
                            <Clock size={16} className="text-indigo-400" /> Average Latency
                        </span>
                        <span className="text-xs font-mono text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded border border-indigo-500/20">
                            Wall Clock
                        </span>
                    </div>
                    <div className="mt-4">
                        <div className="flex items-baseline gap-1">
                            <span className="text-3xl font-bold text-white tracking-tight">
                                {stats?.average_resolution_time_seconds ? stats.average_resolution_time_seconds : '0.0'}
                            </span>
                            <span className="text-base text-slate-400 font-medium">s</span>
                        </div>
                        <p className="text-xs text-slate-400 mt-1">Per issue solve & patch synthesis</p>
                    </div>
                </div>

                {/* Active LLM Provider */}
                <div className="bg-slate-800/40 p-6 rounded-xl border border-slate-700/60 flex flex-col justify-between">
                    <div className="flex items-center justify-between">
                        <span className="text-slate-400 font-medium text-sm flex items-center gap-2">
                            <Cpu size={16} className="text-cyan-400" /> Active Model
                        </span>
                        <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-400">
                            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                            Online
                        </span>
                    </div>
                    <div className="mt-4">
                        <span className="text-lg font-bold text-white tracking-tight truncate block">
                            {stats?.active_model || 'gemini-3.8-flash'}
                        </span>
                        <span className="text-xs text-cyan-400 font-mono uppercase bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-800/40 mt-1 inline-block">
                            Provider: {stats?.active_provider || 'gemini'}
                        </span>
                    </div>
                </div>
            </div>

            {/* Filter Tabs & Search Bar */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-2">
                <div className="flex items-center gap-1.5 bg-slate-900 p-1 rounded-lg border border-slate-800 w-full sm:w-auto overflow-x-auto">
                    <button
                        onClick={() => setSelectedFilter('ALL')}
                        className={cn(
                            "px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap",
                            selectedFilter === 'ALL' 
                                ? "bg-slate-800 text-white shadow-sm" 
                                : "text-slate-400 hover:text-white"
                        )}
                    >
                        All ({tasks.length})
                    </button>
                    <button
                        onClick={() => setSelectedFilter('RESOLVED')}
                        className={cn(
                            "px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap",
                            selectedFilter === 'RESOLVED' 
                                ? "bg-emerald-950/50 text-emerald-400 border border-emerald-900/60" 
                                : "text-slate-400 hover:text-white"
                        )}
                    >
                        Resolved ({resolvedCount})
                    </button>
                    <button
                        onClick={() => setSelectedFilter('FAILED')}
                        className={cn(
                            "px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap",
                            selectedFilter === 'FAILED' 
                                ? "bg-rose-950/50 text-rose-400 border border-rose-900/60" 
                                : "text-slate-400 hover:text-white"
                        )}
                    >
                        Failed ({failedCount})
                    </button>
                    <button
                        onClick={() => setSelectedFilter('PENDING')}
                        className={cn(
                            "px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap",
                            selectedFilter === 'PENDING' 
                                ? "bg-slate-800 text-slate-300" 
                                : "text-slate-400 hover:text-white"
                        )}
                    >
                        Pending ({pendingCount})
                    </button>
                </div>

                <div className="relative w-full sm:w-80">
                    <Search className="absolute left-3 top-2.5 text-slate-500" size={16} />
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search tasks, repos, categories..."
                        className="w-full pl-9 pr-4 py-2 bg-slate-900 border border-slate-800 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                </div>
            </div>

            {/* Benchmark Tasks Table */}
            <div className="border border-slate-800 rounded-xl overflow-hidden shadow-sm bg-slate-900">
                <table className="w-full text-left text-sm text-slate-400">
                    <thead className="text-xs uppercase bg-slate-800/80 text-slate-300">
                        <tr>
                            <th className="px-6 py-4 font-semibold">Instance ID</th>
                            <th className="px-6 py-4 font-semibold">Repository</th>
                            <th className="px-6 py-4 font-semibold">Category</th>
                            <th className="px-6 py-4 font-semibold">Issue Summary</th>
                            <th className="px-6 py-4 font-semibold">Status</th>
                            <th className="px-6 py-4 font-semibold">Latency</th>
                            <th className="px-6 py-4 font-semibold text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50">
                        {filteredTasks.length === 0 ? (
                            <tr>
                                <td colSpan={7} className="px-6 py-12 text-center text-slate-500">
                                    No benchmark instances found matching your criteria.
                                </td>
                            </tr>
                        ) : (
                            filteredTasks.map((t) => {
                                const isCurrentEvaluating = evaluating && evaluatingTaskId === t.instance_id;
                                const firstLine = t.problem_statement.split('\n')[0];

                                return (
                                    <tr 
                                        key={t.instance_id} 
                                        onClick={() => handleOpenTask(t, 'description')}
                                        className="hover:bg-slate-800/40 transition-colors group cursor-pointer"
                                    >
                                        <td className="px-6 py-4 font-mono font-medium text-white group-hover:text-indigo-400 whitespace-nowrap">
                                            <div className="flex items-center gap-1.5">
                                                <span>{t.instance_id}</span>
                                                <Eye size={13} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity" />
                                            </div>
                                        </td>
                                        <td className="px-6 py-4 whitespace-nowrap">
                                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-800/60 border border-slate-700/50 text-slate-300 text-xs">
                                                <GitBranch size={12} className="text-slate-400" />
                                                {t.repo}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4 whitespace-nowrap">
                                            {t.category ? (
                                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700">
                                                    {t.category}
                                                </span>
                                            ) : (
                                                <span className="text-xs text-slate-500">—</span>
                                            )}
                                        </td>
                                        <td className="px-6 py-4 max-w-xs truncate" title={t.problem_statement}>
                                            <span className="text-slate-300 font-medium block truncate">
                                                {firstLine}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4 whitespace-nowrap">
                                            {isCurrentEvaluating ? (
                                                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400 border border-blue-500/30 animate-pulse">
                                                    <RotateCcw size={12} className="animate-spin" /> Evaluating...
                                                </span>
                                            ) : (
                                                <StatusBadge status={t.latest_status || 'PENDING'} />
                                            )}
                                        </td>
                                        <td className="px-6 py-4 font-mono text-xs whitespace-nowrap">
                                            {t.latest_runtime ? `${t.latest_runtime}s` : '—'}
                                        </td>
                                        <td className="px-6 py-4 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                                            <div className="flex items-center justify-end gap-2">
                                                <button
                                                    onClick={() => handleOpenTask(t, 'description')}
                                                    className="px-2.5 py-1 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors border border-slate-700 flex items-center gap-1"
                                                    title="Inspect Issue Description, Analysis & Test Suite"
                                                >
                                                    <Eye size={13} />
                                                    <span>Details</span>
                                                </button>
                                                {t.latest_patch && (
                                                    <button
                                                        onClick={() => handleOpenTask(t, 'patch')}
                                                        className="px-2.5 py-1 text-xs font-medium rounded bg-emerald-950/40 hover:bg-emerald-900/50 text-emerald-300 hover:text-white transition-colors border border-emerald-800/40 flex items-center gap-1"
                                                        title="Inspect Generated Patch Diff"
                                                    >
                                                        <FileCode size={13} />
                                                        <span>Diff</span>
                                                    </button>
                                                )}
                                                <button
                                                    onClick={() => handleRunSingle(t.instance_id)}
                                                    disabled={evaluating}
                                                    className="px-2.5 py-1 text-xs font-medium rounded bg-indigo-600/80 hover:bg-indigo-600 text-white transition-colors disabled:opacity-40 flex items-center gap-1"
                                                    title="Run Evaluation on this Task"
                                                >
                                                    <Play size={12} />
                                                    <span>Run</span>
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

            {/* Comprehensive Task Inspection Modal */}
            <TaskInspectionModal 
                task={inspectedTask} 
                initialTab={modalInitialTab}
                onClose={() => setInspectedTask(null)}
                onRun={handleRunSingle}
                isRunning={evaluating && evaluatingTaskId === inspectedTask?.instance_id}
            />
        </div>
    );
};

export default Evaluation;
