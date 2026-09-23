import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'

interface MarkdownRendererProps {
  content: string
  className?: string
}

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = '',
}: MarkdownRendererProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
        // Customize code blocks
        code({ node, inline, className, children, ...props }) {
          return inline ? (
            <code
              className="px-1.5 py-0.5 rounded bg-[#1a1d24] text-blue-300 text-sm font-mono border border-[#3a3f4b]"
              {...props}
            >
              {children}
            </code>
          ) : (
            <code
              className={`block p-2.5 my-2 rounded-lg bg-[#1a1d24] text-sm font-mono overflow-x-auto border border-[#3a3f4b] ${className}`}
              {...props}
            >
              {children}
            </code>
          )
        },
        // Customize links
        a({ node, children, ...props }) {
          return (
            <a
              className="text-blue-400 hover:text-blue-300 underline transition-colors"
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            >
              {children}
            </a>
          )
        },
        // Customize headings
        h1({ node, children, ...props }) {
          return (
            <h1 className="text-2xl font-bold mb-3 mt-4 text-gray-100" {...props}>
              {children}
            </h1>
          )
        },
        h2({ node, children, ...props }) {
          return (
            <h2 className="text-xl font-bold mb-2 mt-3 text-gray-100" {...props}>
              {children}
            </h2>
          )
        },
        h3({ node, children, ...props }) {
          return (
            <h3 className="text-lg font-semibold mb-2 mt-3 text-gray-200" {...props}>
              {children}
            </h3>
          )
        },
        // Customize lists
        ul({ node, children, ...props }) {
          return (
            <ul className="list-disc list-inside mb-3 space-y-0.5 text-gray-200" {...props}>
              {children}
            </ul>
          )
        },
        ol({ node, children, ...props }) {
          return (
            <ol className="list-decimal list-inside mb-3 space-y-0.5 text-gray-200" {...props}>
              {children}
            </ol>
          )
        },
        li({ node, children, ...props }) {
          return (
            <li className="ml-4 mb-2 text-gray-200 leading-relaxed [&>p]:inline [&>code]:block [&>code]:my-2" {...props}>
              {children}
            </li>
          )
        },
        // Customize paragraphs
        p({ node, children, ...props }) {
          return (
            <p className="mb-2 text-gray-200 leading-relaxed" {...props}>
              {children}
            </p>
          )
        },
        // Customize blockquotes
        blockquote({ node, children, ...props }) {
          return (
            <blockquote
              className="border-l-4 border-blue-500 pl-4 py-2 my-2 bg-[#1a1d24] rounded-r text-gray-300 italic"
              {...props}
            >
              {children}
            </blockquote>
          )
        },
        // Customize tables
        table({ node, children, ...props }) {
          return (
            <div className="overflow-x-auto my-3">
              <table className="min-w-full border border-[#3a3f4b] rounded-lg" {...props}>
                {children}
              </table>
            </div>
          )
        },
        thead({ node, children, ...props }) {
          return (
            <thead className="bg-[#23272f] text-gray-200" {...props}>
              {children}
            </thead>
          )
        },
        tbody({ node, children, ...props }) {
          return (
            <tbody className="bg-[#1a1d24]" {...props}>
              {children}
            </tbody>
          )
        },
        tr({ node, children, ...props }) {
          return (
            <tr className="border-b border-[#3a3f4b]" {...props}>
              {children}
            </tr>
          )
        },
        th({ node, children, ...props }) {
          return (
            <th className="px-4 py-2 text-left font-semibold text-gray-200" {...props}>
              {children}
            </th>
          )
        },
        td({ node, children, ...props }) {
          return (
            <td className="px-4 py-2 text-gray-300" {...props}>
              {children}
            </td>
          )
        },
        // Customize horizontal rules
        hr({ node, ...props }) {
          return <hr className="my-4 border-[#3a3f4b]" {...props} />
        },
      }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
