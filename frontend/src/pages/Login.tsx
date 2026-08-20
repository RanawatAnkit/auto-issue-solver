import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Github } from 'lucide-react';

export const Login: React.FC = () => {
    const { user, isLoading } = useAuth();
    const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-slate-950 font-sans">
                <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-emerald-500"></div>
            </div>
        );
    }

    if (user) {
        return <Navigate to="/" replace />;
    }

    const handleLogin = () => {
        window.location.href = `${API_BASE_URL}/auth/github/login`;
    };

    return (
        <div className="flex items-center justify-center min-h-screen bg-slate-950 font-sans relative overflow-hidden">
            {/* Background elements */}
            <div className="absolute top-0 -left-1/4 w-1/2 h-1/2 bg-blue-500/10 blur-[120px] rounded-full pointer-events-none" />
            <div className="absolute bottom-0 -right-1/4 w-1/2 h-1/2 bg-emerald-500/10 blur-[120px] rounded-full pointer-events-none" />

            <div className="w-full max-w-md p-8 bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl relative z-10 mx-4">
                <div className="text-center mb-8">
                    <div className="h-16 w-16 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 rounded-2xl mx-auto flex items-center justify-center mb-6 shadow-lg shadow-purple-500/20">
                        <Github className="w-8 h-8 text-white" />
                    </div>
                    <h1 className="text-3xl font-bold text-slate-100 mb-2">Welcome Back</h1>
                    <p className="text-slate-400">Sign in to autonomous agent dashboard</p>
                </div>

                <button
                    onClick={handleLogin}
                    className="w-full relative group h-12 flex items-center justify-center gap-3 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl transition-all duration-300 border border-slate-700 hover:border-slate-600 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-900 overflow-hidden"
                >
                    <div className="absolute inset-0 w-full h-full bg-gradient-to-r from-indigo-500/10 to-purple-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
                    <Github className="w-5 h-5 relative z-10" />
                    <span className="font-medium relative z-10">Continue with GitHub</span>
                </button>
            </div>
        </div>
    );
};
