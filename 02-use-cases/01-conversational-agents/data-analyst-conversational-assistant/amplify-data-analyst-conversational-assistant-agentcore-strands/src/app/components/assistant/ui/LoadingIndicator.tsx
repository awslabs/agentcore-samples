'use client';

interface LoadingIndicatorProps {
  loading: boolean;
}

export default function LoadingIndicator({ loading }: LoadingIndicatorProps) {
  if (!loading) return null;

  return (
    <div className="flex items-center gap-3 animate-fade-in">
      <svg className="animate-spin h-5 w-5 text-purple-600" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <div className="py-2 px-4 rounded-2xl shadow-sm">
        <p className="text-base text-gray-700">Answering your question...</p>
      </div>
    </div>
  );
}
