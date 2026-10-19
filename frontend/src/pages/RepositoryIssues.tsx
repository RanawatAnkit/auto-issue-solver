import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { getIssues, errorMessage, errorStatus } from '../services/api';
import { GitHubIssue } from '../types/domain';
import { ArrowLeft, AlertCircle, Clock, CircleDot, Database } from 'lucide-react';

const RepositoryIssues: React.FC = () => {
    const { repositoryId } = useParams<{ repositoryId: string }>();
    const navigate = useNavigate();

    const [issues, setIssues] = useState<GitHubIssue[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchIssues = async () => {
            if (!repositoryId) return;
            setLoading(true);
            try {
                const data = await getIssues(Number(repositoryId));
                setIssues(data);
            } catch (err) {
                if (errorStatus(err) === 401) {
                    navigate('/login');
                } else if (errorStatus(err) === 403) {
                    setError("You do not have permission to access this repository.");
                } else if (errorStatus(err) === 404) {
                    setError("Repository not found.");
                } else {
                    setError(errorMessage(err, 'Failed to fetch issues'));
                }
            } finally {
                setLoading(false);
            }
        };
        fetchIssues();
    }, [repositoryId, navigate]);

    if (loading) {
        return (
            <div className="p-8 flex justify-center mt-20">
                <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2 border-indigo-500"></div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-8">
                <Link to="/repositories" className="text-indigo-400 hover:text-indigo-300 flex items-center gap-2 mb-6 font-medium transition-colors w-fit">
                    <ArrowLeft size={16} /> Back to Repositories
                </Link>
                <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-red-400 font-medium">
                    <p>{error}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="p-8">
            <Link to="/repositories" className="text-indigo-400 hover:text-indigo-300 flex items-center gap-2 mb-6 font-medium transition-colors w-fit">
                <ArrowLeft size={16} /> Back to Repositories
            </Link>

            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-3">
                        <div className="p-2 bg-indigo-500/10 rounded-lg">
                            <Database className="text-indigo-400 w-6 h-6" />
                        </div>
                        Repository Issues
                    </h1>
                </div>
            </div>

            {issues.length === 0 ? (
                <div className="bg-slate-900 border border-slate-800 rounded-2xl p-16 text-center shadow-lg shadow-black/20">
                    <div className="w-20 h-20 bg-slate-800 rounded-full flex items-center justify-center mx-auto mb-6">
                        <AlertCircle className="w-10 h-10 text-slate-500" />
                    </div>
                    <h3 className="text-xl font-semibold text-slate-200 mb-3">No Open Issues Found</h3>
                    <p className="text-slate-500 max-w-sm mx-auto mb-8">
                        This repository has no open issues on GitHub that can be processed.
                    </p>
                </div>
            ) : (
                <div className="space-y-4">
                    {issues.map(issue => (
                        <div key={issue.id} className="bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-slate-700 transition-all shadow-sm flex flex-col md:flex-row gap-4 items-start md:items-center justify-between group">
                            <div className="flex-1">
                                <div className="flex items-center gap-3 mb-2">
                                    <span className="text-green-400 bg-green-400/10 p-1.5 rounded-md flex items-center justify-center">
                                        <CircleDot size={16} />
                                    </span>
                                    <h3 className="text-lg font-semibold text-slate-100 group-hover:text-indigo-400 transition-colors">
                                        {issue.title}
                                    </h3>
                                    <span className="text-slate-500 font-mono text-sm">#{issue.number}</span>
                                </div>
                                <div className="flex items-center gap-4 text-sm text-slate-400 mt-3">
                                    <span className="flex items-center gap-1.5"><Clock size={14} /> {new Date(issue.created_at).toLocaleDateString()}</span>
                                    {issue.labels && issue.labels.length > 0 && (
                                        <div className="flex items-center gap-2">
                                            {issue.labels.slice(0, 3).map(label => (
                                                <span key={label} className="bg-slate-800 px-2 py-0.5 rounded text-xs border border-slate-700">
                                                    {label}
                                                </span>
                                            ))}
                                            {issue.labels.length > 3 && <span className="text-xs text-slate-500">+{issue.labels.length - 3}</span>}
                                        </div>
                                    )}
                                </div>
                            </div>
                            <button
                                onClick={() => navigate(`/repositories/${repositoryId}/issues/${issue.number}`)}
                                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium transition-colors border border-slate-700 whitespace-nowrap focus:outline-none"
                            >
                                View Issue
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
export default RepositoryIssues;
