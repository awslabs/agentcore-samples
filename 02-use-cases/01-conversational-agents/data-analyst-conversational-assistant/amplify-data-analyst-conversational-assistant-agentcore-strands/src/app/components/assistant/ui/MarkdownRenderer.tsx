'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';

interface MarkdownRendererProps {
  content: string;
}

export default function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className="assistant-markdown max-w-full break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          h1: ({ ...props }) => <h1 className="text-[1.4em] font-semibold mt-3 mb-3 leading-tight text-gray-900" {...props} />,
          h2: ({ ...props }) => <h2 className="text-[1.25em] font-semibold mt-2.5 mb-2.5 leading-tight text-gray-900" {...props} />,
          h3: ({ ...props }) => <h3 className="text-[1.15em] font-semibold mt-2 mb-2 leading-tight" {...props} />,
          h4: ({ ...props }) => <h4 className="text-[1.05em] font-semibold mt-2 mb-2 leading-tight" {...props} />,
          p: ({ ...props }) => <p className="mt-0 mb-3.5 leading-relaxed" {...props} />,
          a: ({ ...props }) => <a className="text-blue-600 hover:underline" {...props} />,
          code: ({ className, children, ...props }) => {
            const isBlock = className?.includes('language-');
            return isBlock ? (
              <pre className="bg-gray-50 rounded-md p-3 overflow-x-auto mb-3.5 font-mono text-[85%] leading-[1.45]">
                <code {...props}>{children}</code>
              </pre>
            ) : (
              <code className="bg-black/5 rounded px-1.5 py-0.5 font-mono text-[85%]" {...props}>{children}</code>
            );
          },
          ul: ({ ...props }) => <ul className="pl-6 mt-0 mb-3.5 list-disc" {...props} />,
          ol: ({ ...props }) => <ol className="pl-6 mt-0 mb-3.5 list-decimal" {...props} />,
          li: ({ ...props }) => <li className="mb-1" {...props} />,
          blockquote: ({ ...props }) => <blockquote className="my-3.5 pl-4 text-gray-500 border-l-4 border-gray-300" {...props} />,
          table: ({ ...props }) => <table className="w-full mb-3.5 border-collapse bg-white/20" {...props} />,
          th: ({ ...props }) => <th className="font-semibold p-1.5 border border-gray-300 bg-gray-50/50" {...props} />,
          td: ({ ...props }) => <td className="p-1.5 border border-gray-300" {...props} />,
          hr: () => <hr className="h-px my-4 bg-gray-200 border-0" />,
          strong: ({ ...props }) => <strong className="font-semibold" {...props} />,
          em: ({ ...props }) => <em className="italic" {...props} />,
          img: ({ alt, ...props }) => <img className="max-w-full" alt={alt || ''} {...props} />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
