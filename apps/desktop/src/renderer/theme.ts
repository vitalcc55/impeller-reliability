import { createTheme } from '@mantine/core';

export const impellerTheme = createTheme({
  fontFamily: '"Segoe UI", Arial, sans-serif',
  headings: { fontFamily: '"Golos Text Local", "Segoe UI", Arial, sans-serif' },
  primaryColor: 'navy',
  colors: {
    navy: [
      '#edf3f7',
      '#d9e5ec',
      '#b2cad8',
      '#88acbf',
      '#6592aa',
      '#4f829d',
      '#427996',
      '#326984',
      '#285e77',
      '#102133',
    ],
  },
  cursorType: 'pointer',
  respectReducedMotion: true,
});
