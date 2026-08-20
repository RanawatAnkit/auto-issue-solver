import React, { useEffect, useState } from 'react';
import { getPullRequests } from '../services/api';
import { PullRequestResponse } from '../types/domain';
import { StatusBadge } from '../components/layout/StatusBadge';
import { ExternalLink, GitPullRequest } from 'lucide-react';
import { format } from 'date-fns';

export const PullRequests: React.FC = () => {
    const [prs, setPrs] = useState<PullRequestResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getPullRequests()
            .then((data) => { setPrs(data); setLoading(false); })
            .catch((e) => { setError(e.message); setLoading(false); });
    }, []);

    if (loading) return <div className="p-8 text-slate-400 animate-pulse">Loading Pull Requests...</div>;
    if (error) return <div className="p-8 text-rose-500">Error loading pull requests: {error}</div>;

    return (
        <div className="p-8 max-w-7xl mx-auto space-y-6">
            <h2 className="text-2xl font-bold text-white flex items-center gap-3">
                <GitPullRequest className="text-indigo-500" /> Pull Requests
            </h2>

            {prs.length === 0 ? (
                <div className="bg-slate-800/30 rounded-xl p-8 text-center text-slate-500 border border-slate-800">
                    No pull requests have been created yet.
                </div>
            ) : (
                <div className="border border-slate-800 rounded-xl overflow-hidden shadow-sm bg-slate-900">
                    <table className="w-full text-left text-sm text-slate-400">
                        <thead className="text-xs uppercase bg-slate-800/80 text-slate-300">
                            <tr>
                                <th className="px-6 py-4 font-semibold">ID</th>
                                <th className="px-6 py-4 font-semibold">Branch</th>
                                <th className="px-6 py-4 font-semibold">Issue</th>
                                <th className="px-6 py-4 font-semibold">Status</th>
                                <th className="px-6 py-4 font-semibold">Created</th>
                                <th className="px-6 py-4 font-semibold text-right">Action</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/50">
                            {prs.map((p) => (
                                <tr key={p.id} className="hover:bg-slate-800/30 transition-colors">
                                    <td className="px-6 py-4 font-medium text-white">#{p.id}</td>
                                    <td className="px-6 py-4 font-mono text-xs">{p.branch_name}</td>
                                    <td className="px-6 py-4">#{p.issue_id}</td>
                                    <td className="px-6 py-4"><StatusBadge status={p.status} /></td>
                                    <td className="px-6 py-4">{format(new Date(p.created_at), 'MMM d, HH:mm')}</td>
                                    <td className="px-6 py-4 text-right">
                                        <a
                                            href={p.pr_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-flex items-center gap-1.5 text-indigo-400 hover:text-indigo-300 transition-colors font-medium text-sm"
                                        >
                                            View on GitHub <ExternalLink size={14} />
                                        </a>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};
