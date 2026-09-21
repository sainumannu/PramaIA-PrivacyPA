import { createTheme } from '@mui/material/styles';

// Colore principale dell'app — configurabile in cookiecutter.json (app_color)
const APP_COLOR = '#10b981';
const APP_STORAGE_PREFIX = 'PramaIA-PrivacyManager';

export type ThemeName = 'light' | 'dark' | 'blue';

export const THEME_STORAGE_KEY = `${APP_STORAGE_PREFIX}_theme`;

export const themeOptions: Array<{ value: ThemeName; label: string }> = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'blue', label: 'Blue' },
];

const baseTypography = {
  fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
  h4: { fontWeight: 700 },
  h5: { fontWeight: 600 },
  h6: { fontWeight: 600 },
};

const baseComponents = {
  MuiCard: {
    styleOverrides: {
      root: {
        backgroundImage: 'none',
        border: '1px solid rgba(255,255,255,0.08)',
      },
    },
  },
  MuiButton: {
    styleOverrides: {
      root: { textTransform: 'none' as const, fontWeight: 600, borderRadius: 8 },
    },
  },
};

export const themes = {
  light: createTheme({
    palette: {
      mode: 'light',
      primary: {
        main: APP_COLOR,
      },
      secondary: {
        main: '#06b6d4',
      },
      background: {
        default: '#f4f6fb',
        paper: '#e5e7ef',
      },
      text: {
        primary: '#1a1a2e',
        secondary: APP_COLOR,
      },
    },
    typography: baseTypography,
    shape: { borderRadius: 12 },
    components: baseComponents,
  }),
  dark: createTheme({
    palette: {
      mode: 'dark',
      primary: {
        main: APP_COLOR,
      },
      secondary: {
        main: '#06b6d4',
      },
      background: {
        default: '#0f0f1a',
        paper: '#1a1a2e',
      },
      text: {
        primary: '#f1f5f9',
        secondary: '#94a3b8',
      },
    },
    typography: baseTypography,
    shape: { borderRadius: 12 },
    components: baseComponents,
  }),
  blue: createTheme({
    palette: {
      mode: 'light',
      primary: {
        main: APP_COLOR,
      },
      secondary: {
        main: '#38bdf8',
      },
      background: {
        default: '#e0f2fe',
        paper: '#bae6fd',
      },
      text: {
        primary: '#1e293b',
        secondary: APP_COLOR,
      },
    },
    typography: baseTypography,
    shape: { borderRadius: 12 },
    components: baseComponents,
  }),
} satisfies Record<ThemeName, ReturnType<typeof createTheme>>;

export function isThemeName(value: string): value is ThemeName {
  return themeOptions.some((option) => option.value === value);
}

export function getStoredThemeName(): ThemeName {
  const storedThemeName = localStorage.getItem(THEME_STORAGE_KEY);
  if (!storedThemeName || !isThemeName(storedThemeName)) {
    return 'dark';
  }

  return storedThemeName;
}

