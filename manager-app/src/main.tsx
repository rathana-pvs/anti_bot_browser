import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { MiniWindowApp } from './MiniWindowApp';
import { LiveWindowApp } from './LiveWindowApp';
import { profileIdOfLabel } from './components/live/liveWindows';
import './styles/globals.css';
import { initializeBackendSession } from './services/api';
import { AppDialogProvider } from './components/ui/AppDialogProvider';
import { isDesktopApp } from './services/desktopEnvironment';

/** Extra windows (status, live views) load this same page; Tauri tells them apart by label. */
const windowLabel = async (): Promise<string> => {
  if (!isDesktopApp()) return 'main';
  const { getCurrentWebviewWindow } = await import('@tauri-apps/api/webviewWindow');
  return getCurrentWebviewWindow().label;
};

async function startApp() {
  try {
    await initializeBackendSession();
  } catch (error) {
    console.error('Could not initialize the private backend session', error);
  }

  const label = await windowLabel().catch(() => 'main');
  const liveProfileId = profileIdOfLabel(label);
  if (liveProfileId) {
    ReactDOM.createRoot(document.getElementById('root')!).render(
      <React.StrictMode>
        <AppDialogProvider>
          <LiveWindowApp profileId={liveProfileId} />
        </AppDialogProvider>
      </React.StrictMode>
    );
    return;
  }

  if (label === 'mini') {
    ReactDOM.createRoot(document.getElementById('root')!).render(
      <React.StrictMode>
        <MiniWindowApp />
      </React.StrictMode>
    );
    return;
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
