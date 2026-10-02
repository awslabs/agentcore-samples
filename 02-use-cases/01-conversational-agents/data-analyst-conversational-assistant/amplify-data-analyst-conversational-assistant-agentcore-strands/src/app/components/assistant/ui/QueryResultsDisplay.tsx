'use client';

import { useState, useEffect } from 'react';
import TableView from './TableView';
import type { Answer } from '../types';

interface QueryResultsDisplayProps {
  index: number;
  answer: Answer;
}

export default function QueryResultsDisplay({ index, answer }: QueryResultsDisplayProps) {
  const [expandedQueries, setExpandedQueries] = useState<Record<string, boolean>>({});
  const [collapsedPapers, setCollapsedPapers] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (answer?.queryResults?.length) {
      const initial: Record<string, boolean> = {};
      answer.queryResults.forEach((_, x) => {
        initial[`table_${index}_${x}`] = x !== 0;
      });
      setCollapsedPapers(initial);
    }
  }, [index, answer]);

  const toggleQueryExpand = (key: string) => {
    setExpandedQueries((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const togglePaperCollapse = (key: string) => {
    setCollapsedPapers((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleCopyQuery = (query: string) => {
    navigator.clipboard.writeText(query);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!answer.queryResults) return null;

  return (
    <div>
      {answer.queryResults.map((queryResult, x) => {
        const resultKey = `table_${index}_${x}`;
        const hasResults = queryResult.query_results.length > 0;
        const isCollapsed = collapsedPapers[resultKey];

        return (
          <div
            key={resultKey}
            className="mb-3 overflow-hidden transition-all duration-300"
          >
            {/* Header — always visible */}
            <button
              onClick={() => togglePaperCollapse(resultKey)}
              className={`w-full flex items-center justify-between p-3 cursor-pointer text-left ${
                isCollapsed ? 'bg-gray-50 border border-gray-200 rounded-2xl' : 'border-b border-gray-200'
              }`}
            >
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-600 text-white shrink-0">
                  Result Set {x + 1}
                </span>
                <span className="text-sm text-gray-700 truncate">{queryResult.query_description}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-2">
                <span className={`text-xs font-medium ${hasResults ? 'text-gray-700' : 'text-gray-400'}`}>
                  {hasResults ? `${queryResult.query_results.length} ${queryResult.query_results.length === 1 ? 'record' : 'records'}` : 'No results'}
                </span>
                <svg
                  className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${isCollapsed ? '' : 'rotate-180'}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </div>
            </button>

            {/* Collapsible content */}
            {!isCollapsed && (
              <div className="p-4">
                {/* SQL Query toggle */}
                <div className="flex items-center justify-end gap-1 mb-2">
                  <span className="text-xs text-gray-500">SQL Query:</span>
                  <button
                    onClick={() => handleCopyQuery(queryResult.query)}
                    className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600"
                    title={copied ? 'Copied!' : 'Copy query'}
                    aria-label="Copy SQL query"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </button>
                  <button
                    onClick={() => toggleQueryExpand(resultKey)}
                    className={`p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-transform duration-300 ${expandedQueries[resultKey] ? 'rotate-180' : ''}`}
                    aria-label="Toggle SQL query"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                </div>

                {expandedQueries[resultKey] && (
                  <pre className="mb-3 p-3 bg-gray-50 border border-gray-200 rounded-xl font-mono text-xs whitespace-pre-wrap overflow-x-auto">
                    {queryResult.query}
                  </pre>
                )}

                {hasResults ? (
                  <TableView query_results={queryResult.query_results} />
                ) : (
                  <p className="text-center py-4 text-gray-400">No Data Records</p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
