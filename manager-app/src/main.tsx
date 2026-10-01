import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/globals.css';
import { initializeBackendSession } from './services/api';

async function startApp() {
  try {
    await initializeBackendSession();
  } catch (error) {
    console.error('Could not initialize the private backend session', error);
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

void startApp();
