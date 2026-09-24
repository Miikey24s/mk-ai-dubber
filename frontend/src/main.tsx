import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { ThemeProvider } from './context/ThemeContext';
import { I18nProvider } from './context/I18nContext';
import { JobProvider } from './context/JobContext';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <ThemeProvider>
        <JobProvider>
          <App />
        </JobProvider>
      </ThemeProvider>
    </I18nProvider>
  </React.StrictMode>
);
