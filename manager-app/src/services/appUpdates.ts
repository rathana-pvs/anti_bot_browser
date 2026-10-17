import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

export interface AppUpdateInfo {
  currentVersion: string;
  version: string | null;
  notes: string | null;
  downloaded: boolean;
}
export interface AppUpdateProgress { downloaded: number; total: number | null }
export const getAppVersion = () => invoke<string>('app_update_version');
export const checkAppUpdate = () => invoke<AppUpdateInfo>('check_app_update');
export const downloadAppUpdate = () => invoke<void>('download_app_update');
export const installAppUpdate = () => invoke<void>('install_app_update');
export const listenToAppUpdateProgress = (callback: (progress: AppUpdateProgress) => void) =>
  listen<AppUpdateProgress>('app-update-progress', event => callback(event.payload));
