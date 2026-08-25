import '@mantine/core/styles.css';
import './styles.css';

import { MantineProvider } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import { impellerTheme } from './theme';

import type { ImpellerApi } from '@impeller-reliability/contracts';

const root = document.querySelector('#root');
if (root === null) throw new Error('renderer_root_missing');
const reactRoot = createRoot(root);

interface DesktopApiResolution {
  readonly desktopApi: ImpellerApi | null;
  readonly browserPreview: boolean;
}

async function resolveDesktopApi(): Promise<DesktopApiResolution> {
  if (window.impeller !== undefined) {
    return { desktopApi: window.impeller, browserPreview: false };
  }

  const previewMode = new URLSearchParams(window.location.search).get('preview');
  if (import.meta.env.DEV && (previewMode === 'ready' || previewMode === 'unavailable')) {
    const { createPreviewApi } = await import('./preview-api');
    return { desktopApi: createPreviewApi(previewMode), browserPreview: true };
  }

  return { desktopApi: null, browserPreview: true };
}

async function bootstrap(): Promise<void> {
  const { desktopApi, browserPreview } = await resolveDesktopApi();
  reactRoot.render(
    <StrictMode>
      <MantineProvider defaultColorScheme="light" theme={impellerTheme}>
        <App browserPreview={browserPreview} desktopApi={desktopApi} />
      </MantineProvider>
    </StrictMode>,
  );
}

void bootstrap();
