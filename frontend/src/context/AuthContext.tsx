import React, { createContext, useContext, useEffect, useState } from 'react';
import { getMe, logout } from '../services/api';

interface User {
    id: number;
    github_user_id: string;
    github_login: string;
    github_name: string | null;
    github_avatar_url: string | null;
}

interface AuthContextType {
    user: User | null;
    isLoading: boolean;
    logoutUser: () => Promise<void>;
    checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<User | null>(null);
    const [isLoading, setIsLoading] = useState<boolean>(true);

    const checkAuth = async () => {
        setIsLoading(true);
        try {
            const data = await getMe();
            setUser(data);
        } catch (error) {
            setUser(null);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        checkAuth();
    }, []);

    const logoutUser = async () => {
        try {
            await logout();
            setUser(null);
        } catch (error) {
            console.error("Logout failed", error);
        }
    };

    return (
        <AuthContext.Provider value={{ user, isLoading, logoutUser, checkAuth }}>
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};
