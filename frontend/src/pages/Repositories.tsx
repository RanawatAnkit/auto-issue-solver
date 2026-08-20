import React, { useEffect, useState } from 'react';
import { getRepositories, connectRepository } from '../services/api';
import { Database, Plus, X, ArrowRight, Check } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

export const Repositories: React.FC = () => {
    const navigate = useNavigate();
    const [repos, setRepos] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [showModal, setShowModal] = useState(false);
    const [modalStep, setModalStep] = useState(1);
    const [successRepo, setSuccessRepo] = useState<string | null>(null);
    const [repoFullName, setRepoFullName] = useState("");
    const [connectLoading, setConnectLoading] = useState(false);
    const [connectError, setConnectError] = useState<string | null>(null);

    const loadRepositories = async () => {
        setLoading(true);
        try {
            const data = await getRepositories();
            setRepos(data);
        } catch (err: any) {
            setError(err.message || 'Failed to fetch repositories');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadRepositories();
    }, []);

    const handleConnectClick = () => {
        setModalStep(1);
        setShowModal(true);
        setConnectError(null);
        setRepoFullName("");
        setSuccessRepo(null);
    };

    const handleConnectSubmit = async () => {
        if (!repoFullName.trim()) return;
        setConnectLoading(true);
        setConnectError(null);
        try {
            await connectRepository(repoFullName.trim());
            setSuccessRepo(repoFullName.trim());
            setRepoFullName("");
            setModalStep(4);
            await loadRepositories();
        } catch (err: any) {
            if (err.response) {
                const status = err.response.status;
                if (status === 401) setConnectError("Your GitHub session has expired. Please log in again.");
                else if (status === 403) setConnectError("The GitHub App does not have permission to access this repository. Please check the App installation and repository permissions.");
                else if (status === 404) setConnectError("Auto Issue Solver College is not installed on this repository. Please install the GitHub App and select this repository.");
                else if (status === 409) setConnectError("This repository is already connected.");
                else setConnectError(err.response.data?.detail || "Unable to connect this repository. Please try again.");
            } else {
                setConnectError("Unable to connect this repository. Please try again.");
            }
        } finally {
            setConnectLoading(false);
        }
    };

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
                <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-red-400 font-medium">
                    <p>{error}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="p-8">
            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-3">
                        <div className="p-2 bg-indigo-500/10 rounded-lg">
                            <Database className="text-indigo-400 w-6 h-6" />
                        </div>
                        Repositories
                    </h1>
                    <p className="text-slate-400 text-sm mt-3 mb-6 max-w-xl">
                        Manage connected GitHub repositories securely mapped to your account. Auto-Solver authenticates with your repositories via GitHub App installations.
                    </p>

                    {/* HELP CARD */}
                    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 mb-8 flex flex-col md:flex-row gap-6 items-start md:items-center justify-between shadow-sm">
                        <div>
                            <h3 className="text-base font-semibold text-slate-200 mb-2">How to connect a repository</h3>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2">
                                <ol className="list-decimal list-inside text-sm text-slate-400 space-y-1">
                                    <li>Install Auto Issue Solver College</li>
                                    <li>Select your GitHub account</li>
                                    <li>Select the repository</li>
                                </ol>
                                <ol start={4} className="list-decimal list-inside text-sm text-slate-400 space-y-1">
                                    <li>Return here</li>
                                    <li>Enter owner/repository-name</li>
                                    <li>Click Connect</li>
                                </ol>
                            </div>
                        </div>
                        <button
                            onClick={() => {
                                window.open("https://github.com/apps/auto-issue-solver-college", "_blank", "noopener,noreferrer");
                            }}
                            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium transition-colors border border-slate-700 flex items-center gap-2 whitespace-nowrap focus:outline-none"
                        >
                            Open GitHub App ↗
                        </button>
                    </div>
                </div>
                <button
                    onClick={handleConnectClick}
                    className="flex items-center gap-2 px-4 py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white rounded-lg transition-all shadow-lg shadow-indigo-500/20 font-medium active:scale-95 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-950"
                >
                    <Plus size={18} />
                    Connect Repository
                </button>
            </div>

            {repos.length === 0 ? (
                <div className="bg-slate-900 border border-slate-800 rounded-2xl p-16 text-center shadow-lg shadow-black/20">
                    <div className="w-20 h-20 bg-slate-800 rounded-full flex items-center justify-center mx-auto mb-6">
                        <Database className="w-10 h-10 text-slate-500" />
                    </div>
                    <h3 className="text-xl font-semibold text-slate-200 mb-3">No Repositories Connected</h3>
                    <p className="text-slate-500 max-w-sm mx-auto mb-8">
                        You haven't integrated any repositories yet. Click the button above to begin executing autonomous agent operations.
                    </p>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {repos.map((repo) => (
                        <div key={repo.id} className="bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-slate-700 transition-all hover:shadow-lg hover:shadow-indigo-500/5 group relative overflow-hidden flex flex-col">
                            <div className="absolute top-0 right-0 p-4 opacity-5 group-hover:opacity-10 transition-opacity">
                                <Database size={80} />
                            </div>

                            <h3 className="text-lg font-semibold text-slate-100 group-hover:text-indigo-400 transition-colors mb-1 relative z-10">{repo.name}</h3>
                            <p className="text-sm text-slate-500 mb-6 relative z-10 font-mono">{repo.full_name}</p>

                            <div className="mt-auto flex items-center justify-between text-xs text-slate-400 relative z-10 pt-4 border-t border-slate-800">
                                <span className="bg-slate-800 px-3 py-1 rounded-full border border-slate-700 font-medium">
                                    {repo.default_branch}
                                </span>

                                <button
                                    onClick={() => navigate(`/repositories/${repo.id}/issues`)}
                                    className="px-3 py-1.5 bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500 hover:text-white rounded-lg transition-colors font-medium flex items-center gap-1 focus:outline-none"
                                >
                                    View Issues <ArrowRight size={14} />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* CONNECT MODAL */}
            {showModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
                    <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-lg shadow-2xl relative overflow-hidden">

                        {/* HEADER */}
                        <div className="flex items-center justify-between p-6 border-b border-slate-800">
                            <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
                                {modalStep === 4 ? (
                                    <>
                                        <div className="bg-green-500/20 text-green-400 p-1 rounded-full text-xs">
                                            <Check size={16} />
                                        </div>
                                        Repository Connected!
                                    </>
                                ) : (
                                    <>
                                        <Database className="text-indigo-400 w-5 h-5" />
                                        Connect GitHub Repository
                                    </>
                                )}
                            </h2>
                            <button
                                onClick={() => setShowModal(false)}
                                className="text-slate-400 hover:text-slate-200 transition-colors focus:outline-none"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        {/* STEP 1 */}
                        {modalStep === 1 && (
                            <>
                                <div className="p-6 pb-2">
                                    <p className="text-xs text-indigo-400 font-bold tracking-wider uppercase mb-1">Step 1 of 3</p>
                                    <h3 className="text-lg font-semibold text-slate-200 mb-3">Install GitHub App</h3>
                                    <p className="text-slate-400 text-sm mb-4">
                                        Before connecting a repository, you need to install Auto Issue Solver College on your GitHub account and give it access to the repository you want Auto-Solver to work with.
                                    </p>

                                    <div className="bg-slate-950 border border-slate-800 rounded-lg p-4 mb-4 shadow-inner">
                                        <ul className="text-sm text-slate-300 space-y-2 list-decimal pl-4">
                                            <li>Click "Install GitHub App" below.</li>
                                            <li>GitHub will open the installation page in a new tab.</li>
                                            <li>Select your GitHub account.</li>
                                            <li>Choose "Only select repositories".</li>
                                            <li>Select your chosen repository.</li>
                                            <li>Click "Install".</li>
                                        </ul>
                                    </div>

                                    <p className="text-xs text-slate-500 italic mb-4">
                                        Note: Your GitHub password, private key, and backend credentials are never required.
                                    </p>
                                </div>
                                <div className="p-6 pt-0 flex items-center justify-between gap-3">
                                    <button
                                        onClick={() => setModalStep(2)}
                                        className="text-slate-400 hover:text-slate-300 font-medium transition-colors focus:outline-none"
                                    >
                                        Skip (Already Installed)
                                    </button>
                                    <button
                                        onClick={() => {
                                            window.open("https://github.com/apps/auto-issue-solver-college", "_blank", "noopener,noreferrer");
                                            setModalStep(2);
                                        }}
                                        className="px-5 py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white rounded-lg font-medium transition-colors flex items-center gap-2 focus:outline-none shadow-lg shadow-indigo-500/20"
                                    >
                                        Install GitHub App ↗
                                    </button>
                                </div>
                            </>
                        )}

                        {/* STEP 2 */}
                        {modalStep === 2 && (
                            <>
                                <div className="p-6">
                                    <p className="text-xs text-indigo-400 font-bold tracking-wider uppercase mb-1">Step 2 of 3</p>
                                    <h3 className="text-lg font-semibold text-slate-200 mb-4">Return to Auto Issue Solver</h3>

                                    <div className="flex items-center gap-3 bg-green-500/10 border border-green-500/20 p-5 rounded-xl mb-6 shadow-sm">
                                        <div className="bg-green-500/20 p-1.5 rounded-full shrink-0">
                                            <Check className="text-green-400" size={18} />
                                        </div>
                                        <p className="text-sm text-green-300 font-medium">GitHub App installation completed</p>
                                    </div>

                                    <p className="text-slate-400 text-sm mb-4">
                                        After installing the GitHub App and choosing your repository, return to this tab and continue.
                                    </p>
                                </div>
                                <div className="p-6 pt-0 flex items-center justify-between border-t border-slate-800 mt-2">
                                    <button
                                        onClick={() => setModalStep(1)}
                                        className="text-slate-400 hover:text-slate-300 font-medium transition-colors px-2 py-1 focus:outline-none"
                                    >
                                        Back
                                    </button>
                                    <button
                                        onClick={() => setModalStep(3)}
                                        className="px-5 py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white rounded-lg font-medium transition-colors flex items-center gap-2 focus:outline-none shadow-lg shadow-indigo-500/20"
                                    >
                                        I've Installed the GitHub App
                                    </button>
                                </div>
                            </>
                        )}

                        {/* STEP 3 */}
                        {modalStep === 3 && (
                            <>
                                <div className="p-6">
                                    <p className="text-xs text-indigo-400 font-bold tracking-wider uppercase mb-1">Step 3 of 3</p>
                                    <h3 className="text-lg font-semibold text-slate-200 mb-5">Connect Repository</h3>

                                    <div className="mb-2">
                                        <label className="block text-sm font-medium text-slate-300 mb-2">GitHub Repository</label>
                                        <input
                                            type="text"
                                            placeholder="e.g. Ankit7474738/Sample"
                                            value={repoFullName}
                                            onChange={(e) => setRepoFullName(e.target.value)}
                                            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-3 text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 placeholder-slate-600 shadow-inner"
                                            disabled={connectLoading}
                                        />
                                        <div className="mt-2 text-xs text-slate-400 font-mono">Format: owner/repository-name</div>
                                        <p className="text-xs text-indigo-300/80 mt-1 italic">Enter the repository you selected when installing the GitHub App.</p>
                                    </div>

                                    {connectError && (
                                        <div className="mt-5 p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-sm text-red-400 flex flex-col gap-3">
                                            <p className="font-medium leading-relaxed">{connectError}</p>
                                            {connectError.includes("not be found") && (
                                                <button
                                                    onClick={() => setModalStep(1)}
                                                    className="self-start text-xs bg-slate-800 hover:bg-slate-700 text-slate-200 px-3 py-1.5 rounded-md transition font-medium focus:outline-none"
                                                >
                                                    Install GitHub App
                                                </button>
                                            )}
                                        </div>
                                    )}
                                </div>
                                <div className="p-6 pt-0 flex items-center justify-between border-t border-slate-800 mt-2">
                                    <button
                                        onClick={() => setModalStep(2)}
                                        disabled={connectLoading}
                                        className="text-slate-400 hover:text-slate-300 font-medium transition-colors px-2 py-1 focus:outline-none disabled:opacity-50"
                                    >
                                        Back
                                    </button>
                                    <button
                                        onClick={handleConnectSubmit}
                                        disabled={connectLoading || !repoFullName.trim()}
                                        className="px-5 py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 focus:outline-none shadow-lg shadow-indigo-500/20"
                                    >
                                        {connectLoading && <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white"></div>}
                                        Connect Repository
                                    </button>
                                </div>
                            </>
                        )}

                        {/* STEP 4 (SUCCESS) */}
                        {modalStep === 4 && (
                            <>
                                <div className="p-8 text-center border-t border-slate-800 bg-slate-900/50">
                                    <div className="w-20 h-20 bg-green-500/10 rounded-full flex items-center justify-center mx-auto mb-5 border border-green-500/20 shadow-inner">
                                        <Check size={40} className="text-green-400" />
                                    </div>
                                    <h3 className="text-xl font-bold text-slate-100 mb-2 font-mono">{successRepo}</h3>
                                    <p className="text-slate-400 text-sm mb-8 max-w-sm mx-auto leading-relaxed">
                                        Auto-Solver can now read issues, modify code, run tests, and create pull requests for this repository autonomously.
                                    </p>
                                    <div className="flex items-center justify-center gap-4">
                                        <button
                                            onClick={() => setShowModal(false)}
                                            className="px-8 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium transition-colors focus:outline-none shadow-sm border border-slate-700"
                                        >
                                            Done
                                        </button>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default Repositories;
