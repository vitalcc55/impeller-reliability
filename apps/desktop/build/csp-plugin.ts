import type { Plugin } from 'vite';

const baseCsp = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
];

export function cspPlugin(command: 'build' | 'serve'): Plugin {
  const connectSource = command === 'serve' ? "'self' ws://127.0.0.1:5173" : "'none'";
  const policy = [...baseCsp, `connect-src ${connectSource}`].join('; ');
  return {
    name: 'impeller-csp',
    transformIndexHtml: (html) => html.replace('__IMPELLER_CSP__', policy),
  };
}
