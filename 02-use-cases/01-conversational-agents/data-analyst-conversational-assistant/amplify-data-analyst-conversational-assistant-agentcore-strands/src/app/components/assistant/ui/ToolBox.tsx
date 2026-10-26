'use client';

import type { ToolItem } from '../types';

interface ToolBoxProps {
  item: ToolItem;
  isLoading?: boolean;
}

export default function ToolBox({ item, isLoading = false }: ToolBoxProps) {
  const hasInputs =
    item.inputs &&
    ((typeof item.inputs === 'object' && Object.keys(item.inputs).length > 0) ||
      (typeof item.inputs !== 'object' && String(item.inputs).trim() !== ''));

  return (
    <div
      className={`
        p-3 rounded-xl overflow-hidden
        bg-purple-600/5 border border-purple-600/30 border-l-4 border-l-purple-600
        mb-3 relative transition-all duration-400
        hover:-translate-y-0.5 hover:bg-purple-600/10 hover:border-purple-600/40
        hover:shadow-[0_0_0_1px_rgba(84,37,175,0.3),0_0_8px_rgba(84,37,175,0.15)]
        ${isLoading ? 'animate-pulse' : ''}
      `}
    >
      <div className="flex items-center gap-3">
        {/* Icon */}
        <div className="w-8 h-8 rounded-full bg-purple-600 flex items-center justify-center shrink-0">
          {isLoading ? (
            <svg className="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" />
            </svg>
          )}
        </div>

        {/* Tool info */}
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-sm text-gray-800 uppercase">{item.name}</p>
          {hasInputs && (
            <div className="mt-1.5">
              {typeof item.inputs === 'object' ? (
                Object.entries(item.inputs).map(([key, value]) => (
                  <p key={key} className="text-sm text-gray-500 leading-relaxed break-words mb-0.5">
                    <span className="text-purple-600 font-medium">{key}:</span>{' '}
                    {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                  </p>
                ))
              ) : (
                <p className="text-sm text-gray-500 leading-relaxed break-words">{String(item.inputs)}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
