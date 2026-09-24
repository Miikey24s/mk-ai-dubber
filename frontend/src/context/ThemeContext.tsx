import React, { createContext, useContext, useState, useEffect } from 'react';
import { AppMode, AppTheme } from '@/types';

interface ThemeContextType {
  theme: AppTheme;
  setTheme: (theme: AppTheme) => void;
  toggleTheme: () => void;
  mode: AppMode;
  setMode: (mode: AppMode) => void;
  toggleMode: () => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [theme, setThemeState] = useState<AppTheme>(() => {
    const saved = localStorage.getItem('vi_dubber_theme');
    return (saved === 'dark' || saved === 'light') ? saved : 'dark';
  });

  const [mode, setModeState] = useState<AppMode>(() => {
    const saved = localStorage.getItem('vi_dubber_mode');
    return (saved === 'standard' || saved === 'engineer') ? saved : 'engineer';
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
      root.classList.remove('light');
    } else {
      root.classList.remove('dark');
      root.classList.add('light');
    }
    localStorage.setItem('vi_dubber_theme', theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem('vi_dubber_mode', mode);
  }, [mode]);

  const setTheme = (t: AppTheme) => setThemeState(t);
  const toggleTheme = () => setThemeState(prev => (prev === 'dark' ? 'light' : 'dark'));

  const setMode = (m: AppMode) => setModeState(m);
  const toggleMode = () => setModeState(prev => (prev === 'standard' ? 'engineer' : 'standard'));

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme, mode, setMode, toggleMode }}>
      {children}
    </ThemeContext.Provider>
  );
};

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};
