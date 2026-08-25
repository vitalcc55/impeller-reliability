import react from '@vitejs/plugin-react';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';

import packageMetadata from './package.json';

const bundledWorkspacePackages = [
  '@impeller-reliability/application',
  '@impeller-reliability/contracts',
];

export default defineConfig({
  main: {
    define: { __APPLICATION_VERSION__: JSON.stringify(packageMetadata.version) },
    plugins: [externalizeDepsPlugin({ exclude: bundledWorkspacePackages })],
  },
  preload: {
    // Sandboxed preload cannot resolve arbitrary Node packages at runtime.
    plugins: [externalizeDepsPlugin({ exclude: [...bundledWorkspacePackages, 'zod'] })],
    build: {
      rollupOptions: { output: { entryFileNames: '[name].cjs', format: 'cjs' } },
    },
  },
  renderer: {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
    },
  },
});
