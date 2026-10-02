import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/globals.css';
import { initializeBackendSession } from './services/api';
import { AppDialogProvider } from './components/ui/AppDialogProvider';

async function startApp() {
  try {
    await initializeBackendSession();
  } catch (error) {
    console.error('Could not initialize the private backend session', error);
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <AppDialogProvider>
        <App />
      </AppDialogProvider>
    </React.StrictMode>
  );
}

void startApp();
