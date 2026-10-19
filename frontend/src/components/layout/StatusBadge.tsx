import React from 'react';
import { cn } from '../../utils/cn';

export const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
    let colorClass = 'bg-slate-700 text-slate-300';

    if (status.includes('SUCCESS') || status === 'COMPLETED' || status === 'PR_CREATED' || status === 'RESOLVED' || status === 'CODE_CHANGED' || status === 'PATCH_APPLIED' || status === 'COMMITTED' || status === 'PUSHED') {
        colorClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
    } else if (status.includes('FAIL') || status.includes('EXHAUSTED') || status === 'CANCELLED') {
        colorClass = 'bg-rose-500/20 text-rose-400 border border-rose-500/30';
    } else if (['CODING', 'TESTING', 'REPAIRING', 'REPAIR_PLANNING', 'VALIDATING', 'BRANCH_CREATED', 'PUSHING', 'COMMITTING', 'CREATING_PR'].includes(status)) {
        colorClass = 'bg-blue-500/20 text-blue-400 border border-blue-500/30';
    } else if (status === 'RECEIVED' || status === 'PENDING' || status === 'QUEUED') {
        colorClass = 'bg-slate-700/50 text-slate-400 border border-slate-700';
    } else if (status === 'PLAN_READY') {
        colorClass = 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30';
    } else {
        colorClass = 'bg-amber-500/20 text-amber-400 border border-amber-500/30';
    }

    return (
        <span className={cn('px-2.5 py-0.5 rounded-full text-xs font-medium tracking-wide', colorClass)}>
            {status.replace(/_/g, ' ')}
        </span>
    );
};
