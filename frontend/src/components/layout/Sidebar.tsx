import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Activity, GitPullRequest, LogOut, Database } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useAuth } from '../../context/AuthContext';

export const Sidebar: React.FC = () => {
    const { user, logoutUser } = useAuth();

    let displayName = "User";
    let avatarLetter = "U";
    if (user) {
        displayName = user.github_name?.trim() || user.github_login?.trim() || "User";
        avatarLetter = displayName.charAt(0).toUpperCase();
    }
    const links = [
        { to: '/', label: 'Dashboard', icon: LayoutDashboard },
        { to: '/agent-runs', label: 'Agent Runs', icon: Activity },
        { to: '/repositories', label: 'Repositories', icon: Database },
        { to: '/pull-requests', label: 'Pull Requests', icon: GitPullRequest },
    ];

    return (
        <aside className="w-64 bg-slate-900 border-r border-slate-800 min-h-screen flex flex-col items-stretch text-slate-300">
            <div className="h-16 flex items-center px-6 border-b border-slate-800">
                <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
                    <Activity className="text-indigo-500" /> Auto-Solver
                </h1>
            </div>
            <nav className="flex-1 px-4 py-6 space-y-2">
                {links.map((link) => (
                    <NavLink
                        key={link.to}
                        to={link.to}
                        className={({ isActive }) => cn(
                            'flex items-center gap-3 px-3 py-2.5 rounded-md font-medium transition-colors',
                            isActive ? 'bg-indigo-500/10 text-indigo-400' : 'hover:bg-slate-800/50 hover:text-slate-100'
                        )}
                    >
                        <link.icon size={20} />
                        {link.label}
                    </NavLink>
                ))}
            </nav>
            <div className="mt-auto p-4 border-t border-slate-800">
                {user && (
                    <div className="flex items-center gap-3 px-2">
                        {user.github_avatar_url ? (
                            <img src={user.github_avatar_url} alt={displayName} className="w-8 h-8 rounded-full border border-slate-700" />
                        ) : (
                            <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center font-bold text-white uppercase">{avatarLetter}</div>
                        )}
                        <div className="flex-1 overflow-hidden">
                            <p className="text-sm font-medium text-slate-200 truncate">{displayName}</p>
                        </div>
                        <button onClick={logoutUser} className="text-slate-500 hover:text-red-400 p-1 rounded transition-colors focus:outline-none" title="Logout">
                            <LogOut size={18} />
                        </button>
                    </div>
                )}
            </div>
        </aside>
    );
};
