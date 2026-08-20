import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { getRepositoryIssue, solveIssue } from '../services/api';
import { GitHubIssue } from '../types/domain';
import { ArrowLeft, Clock, CircleDot, Activity } from 'lucide-react';

const IssueDetails: React.FC = () => {
    const { repositoryId, issueNumber } = useParams<{ repositoryId: string, issueNumber: string }>();
    const navigate = useNavigate();

    const [issue, setIssue] = useState<GitHubIssue | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [solveLoading, setSolveLoading] = useState(false);
    const [solveError, setSolveError] = useState<string | null>(null);

    useEffect(() => {
        const fetchIssue = async () => {
            if (!repositoryId || !issueNumber) return;
            setLoading(true);
            try {
                const data = await getRepositoryIssue(repositoryId, issueNumber);
                setIssue(data);
            } catch (err: any) {
                if (err.response?.status === 401) {
                    navigate('/login');
                } else if (err.response?.status === 404) {
                    setError("Issue not found or inaccessible.");
                } else {
                    setError(err.message || 'Failed to fetch issue details');
                }
            } finally {
                setLoading(false);
            }
        };
        fetchIssue();
    }, [repositoryId, issueNumber, navigate]);

    const handleSolve = async () => {
        if (!repositoryId || !issueNumber) return;
        setSolveLoading(true);
        setSolveError(null);
        try {
            const resp = await solveIssue(repositoryId, issueNumber);
            navigate(`/agent-runs/${resp.run_id}`);
        } catch (err: any) {
            if (err.response?.status === 409) {
                setSolveError("An Auto-Solver run is already active for this issue.");
            } else if (err.response?.status === 401) {
                navigate('/login');
            } else if (err.response?.status === 403) {
                setSolveError("Unauthorized repository access.");
            } else {
                setSolveError("Internal server error. Failed to dispatch the auto-solver.");
            }
            setSolveLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="p-8 flex justify-center mt-20">
                <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2 border-indigo-500"></div>
            </div>
        );
    }

    if (error || !issue) {
        return (
            <div className="p-8">
                <Link to={`/repositories/${repositoryId}/issues`} className="text-indigo-400 hover:text-indigo-300 flex items-center gap-2 mb-6 font-medium transition-colors w-fit">
                    <ArrowLeft size={16} /> Back to Issues
                </Link>
                <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-red-400 font-medium">
                    <p>{error || "Failed to load."}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="p-8 max-w-5xl mx-auto">
            <Link to={`/repositories/${repositoryId}/issues`} className="text-indigo-400 hover:text-indigo-300 flex items-center gap-2 mb-6 font-medium transition-colors w-fit">
                <ArrowLeft size={16} /> Back to Issues
            </Link>

            <div className="bg-slate-900 border border-slate-800 rounded-2xl shadow-xl overflow-hidden mb-6">
                <div className="p-8 border-b border-slate-800">
                    <div className="flex items-center gap-3 mb-4">
                        <span className="text-green-400 bg-green-400/10 px-2.5 py-1 rounded-md flex items-center gap-1.5 text-sm font-medium">
                            <CircleDot size={14} /> {issue.state || 'open'}
                        </span>
                        <span className="text-slate-500 font-mono text-sm">Issue #{issue.number}</span>
                    </div>
                    <h1 className="text-2xl font-bold text-slate-100 mb-4">{issue.title}</h1>
                    <div className="flex flex-wrap gap-2 mb-6">
                        {issue.labels?.map((l: string) => (
                            <span key={l} className="bg-slate-800 px-3 py-1 rounded-full text-xs font-medium border border-slate-700 text-slate-300">
                                {l}
                            </span>
                        ))}
                    </div>
                    <div className="flex items-center gap-6 text-sm text-slate-400">
                        <span className="flex items-center gap-2"><Clock size={16} /> Opened {new Date(issue.created_at).toLocaleDateString()}</span>
                    </div>
                </div>

                <div className="p-8 bg-slate-900/50 prose prose-invert max-w-none text-slate-300 border-b border-slate-800">
                    {issue.body ? (
                        <div dangerouslySetInnerHTML={{ __html: issue.body.replace(/\n/g, '<br />') }} />
                    ) : (
                        <p className="italic text-slate-500">No description provided for this issue.</p>
                    )}
                </div>

                <div className="p-6 bg-slate-800/20 flex flex-col items-end gap-3">
                    {solveError && (
                        <div className="bg-red-500/10 border border-red-500/20 px-4 py-2 rounded-lg text-sm text-red-400 font-medium">
                            {solveError}
                        </div>
                    )}
                    <button
                        onClick={handleSolve}
                        disabled={solveLoading}
                        className="flex items-center gap-2 px-6 py-3 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl transition-all shadow-lg shadow-indigo-500/20 font-bold text-lg active:scale-95 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-900 disabled:opacity-50 disabled:scale-100"
                    >
                        {solveLoading ? (
                            <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-white mr-2"></div>
                        ) : (
                            <Activity size={20} />
                        )}
                        Solve with Auto-Solver
                    </button>
                </div>
            </div>
        </div>
    );
};
export default IssueDetails;
