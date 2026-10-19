import React, { createContext, useContext, useEffect, useState } from 'react';
import { getMe, logout } from '../services/api';
import { UserProfile as User } from '../types/domain';

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

// Exporting the hook next to its provider is the conventional pattern for contexts.
// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};
