import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Make URLs in text clickable and convert newlines to <br> tags
 */
export function makeUrlsClickable(text: string): string {
  // First, convert literal \n strings to <br> tags (handles escaped newlines from backend)
  let textWithBreaks = text.replace(/\\n/g, '<br>')

  // Also convert actual newline characters to <br> tags
  textWithBreaks = textWithBreaks.replace(/\n/g, '<br>')

  // Then, convert URLs to clickable links
  const urlPattern = /https?:\/\/(?:[-\w.])+(?:\:[0-9]+)?(?:\/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:\#(?:[\w.])*)?)?/g

  return textWithBreaks.replace(urlPattern, (url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" class="text-blue-400 underline hover:text-blue-300">${url}</a>`
  })
}

/**
 * Generate a random UUID v4
 */
export function generateUUID(): string {
  return crypto.randomUUID()
}

/**
 * Format elapsed time
 */
export function formatElapsedTime(seconds: number): string {
  if (seconds < 1) {
    return `${(seconds * 1000).toFixed(0)}ms`
  }
  return `${seconds.toFixed(2)}s`
}
