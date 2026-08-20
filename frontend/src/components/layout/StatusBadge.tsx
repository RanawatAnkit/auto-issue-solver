import React from 'react';
import { cn } from '../../utils/cn';

export const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
    let colorClass = 'bg-slate-700 text-slate-300';

    if (status.includes('SUCCESS') || status === 'COMPLETED' || status === 'PR_CREATED' || status === 'CODE_CHANGED') {
        colorClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
    } else if (status.includes('FAIL') || status.includes('EXHAUSTED')) {
        colorClass = 'bg-rose-500/20 text-rose-400 border border-rose-500/30';
    } else if (['CODING', 'TESTING', 'REPAIRING', 'REPAIR_PLANNING', 'PUSHING', 'COMMITTING'].includes(status)) {
        colorClass = 'bg-blue-500/20 text-blue-400 border border-blue-500/30';
    } else if (status === 'RECEIVED' || status === 'PENDING') {
        colorClass = 'bg-slate-700/50 text-slate-400 border border-slate-700';
    } else {
        colorClass = 'bg-amber-500/20 text-amber-400 border border-amber-500/30';
    }

    return (
        <span className={cn('px-2.5 py-0.5 rounded-full text-xs font-medium tracking-wide', colorClass)}>
            {status.replace(/_/g, ' ')}
        </span>
    );
};
