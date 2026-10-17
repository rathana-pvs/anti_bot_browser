import { ReactNode } from 'react';

export function EmptyState({ icon, title, description, action }: {
  icon?: ReactNode; title: string; description?: string; action?: ReactNode;
}) {
  return <div className="flex flex-col items-center justify-center rounded-panel border border-dashed border-zinc-800 p-8 text-center">
    {icon && <div className="mb-3 text-zinc-500">{icon}</div>}
    <h3 className="text-sm font-semibold text-zinc-200">{title}</h3>
    {description && <p className="mt-1 max-w-md text-xs text-zinc-500">{description}</p>}
    {action && <div className="mt-4">{action}</div>}
  </div>;
}
