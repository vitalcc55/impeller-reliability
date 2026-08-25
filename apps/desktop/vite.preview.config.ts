import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

import { cspPlugin } from './build/csp-plugin';

export default defineConfig({
  root: resolve(import.meta.dirname, 'src/renderer'),
  plugins: [react(), cspPlugin('serve')],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
});
