import { invoke } from '@tauri-apps/api/core';
import { listen, UnlistenFn } from '@tauri-apps/api/event';

export type SetupStepStatus = 'ready' | 'waiting' | 'action_required';

export interface SetupStep {
  id: string;
  title: string;
  description: string;
  status: SetupStepStatus;
  detail: string;
}

export interface GpuCompatibility {
  hostGpu: string | null;
  wslGpuVisible: boolean;
  browserAccelerationAvailable: boolean;
  cudaRuntimeAvailable: boolean;
  runtimeInstallRequired: boolean;
  cudaDevice: string | null;
  ocrDevice: 'cpu' | 'cuda';
  ocrLabel: string;
  fallbackReason: string | null;
}

export interface SetupSnapshot {
  platform: 'windows' | 'linux';
  ready: boolean;
  restartRequired: boolean;
  gpu: GpuCompatibility;
  steps: SetupStep[];
}

export interface SetupRunResult {
  success: boolean;
  exitCode: number;
  restartRequired: boolean;
  message: string;
}

export interface SetupLogEvent {
  stream: 'system' | 'stdout' | 'stderr';
  message: string;
}

export const isDesktopApp = (): boolean =>
  typeof window !== 'undefined' && (
    '__TAURI_INTERNALS__' in window ||
    '__TAURI__' in window ||
    window.location.protocol === 'tauri:' ||
    window.location.hostname === 'tauri.localhost'
  );

export async function fetchSetupStatus(): Promise<SetupSnapshot> {
  return invoke<SetupSnapshot>('get_setup_status');
}

export async function fetchSetupLog(): Promise<string[]> {
  return invoke<string[]>('get_setup_log');
}

export async function startSetup(repair: boolean): Promise<SetupRunResult> {
  return invoke<SetupRunResult>('run_setup', { repair });
}

export async function recoverServices(): Promise<SetupRunResult> {
  return invoke<SetupRunResult>('recover_services');
}

export async function listenToSetupLogs(
  listener: (event: SetupLogEvent) => void,
): Promise<UnlistenFn> {
  return listen<SetupLogEvent>('setup-log', (event) => listener(event.payload));
}
