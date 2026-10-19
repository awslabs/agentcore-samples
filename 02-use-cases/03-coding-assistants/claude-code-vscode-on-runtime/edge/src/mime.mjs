// Content types for everything the edge serves. The build records the type of each baked file in the
// manifest, so the handler never guesses at request time.

const TEXT = 'text/plain; charset=utf-8';

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.cjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.code-snippets': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.bmp': 'image/bmp',
  '.ico': 'image/x-icon',
  '.wasm': 'application/wasm',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.pdf': 'application/pdf',
  '.xml': 'application/xml; charset=utf-8',
  '.tmlanguage': 'application/xml; charset=utf-8',
  '.plist': 'application/xml; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.txt': TEXT,
  '.ts': TEXT,
  '.mts': TEXT,
  '.cts': TEXT,
  '.scm': TEXT,
  '.sh': TEXT,
  '.zsh': TEXT,
  '.fish': TEXT,
  '.ps1': TEXT,
  '.psm1': TEXT,
  '.py': TEXT,
  '.pl': TEXT,
  '.yml': TEXT,
  '.yaml': TEXT,
  '.cfg': TEXT,
  '.scss': TEXT,
  '.ttx': TEXT,
  '.mf': TEXT,
};

const TEXT_NAMES = new Set(['license', 'makefile', 'readme', 'notice', 'authors', 'changelog']);

export function mimeFor(path) {
  const name = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return TEXT_NAMES.has(name) ? TEXT : 'application/octet-stream';
  return TYPES[name.slice(dot)] ?? 'application/octet-stream';
}

// Worth pre-compressing: text, JSON, SVG, WebAssembly and the uncompressed font formats. woff/woff2,
// images and audio are already compressed.
export function isCompressible(mime) {
  const base = mime.split(';')[0].trim();
  return base.startsWith('text/')
    || base === 'application/json'
    || base === 'application/manifest+json'
    || base === 'application/xml'
    || base === 'application/wasm'
    || base === 'image/svg+xml'
    || base === 'image/x-icon'
    || base === 'image/bmp'
    || base === 'font/ttf'
    || base === 'font/otf';
}
