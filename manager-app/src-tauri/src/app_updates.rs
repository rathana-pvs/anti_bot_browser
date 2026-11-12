//! Whole-app updates. Only these guarded commands may install an update.
use super::{
    backend_is_running, spawn_backend, terminate_backend, verify_runtime_install_idle,
    BackendProcess, SetupProcess,
};
use serde::Serialize;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
struct Pending {
    update: Option<Update>,
    bytes: Option<Arc<Vec<u8>>>,
}
#[derive(Default)]
pub struct AppUpdates {
    pending: Mutex<Pending>,
    busy: AtomicBool,
}
struct Busy<'a>(&'a AtomicBool);
impl Drop for Busy<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
struct SetupLease<'a>(&'a Mutex<bool>);
impl Drop for SetupLease<'_> {
    fn drop(&mut self) {
        if let Ok(mut running) = self.0.lock() {
            *running = false;
        }
    }
}
fn update_maintenance(enabled: bool) -> Result<(), String> {
    if !backend_is_running() {
        return Ok(());
    }
    let token =
        std::env::var("MANAGER_API_TOKEN").map_err(|_| "Restart the app before updating")?;
    let address = SocketAddr::from(([127, 0, 0, 1], super::manager_api_port()));
    let mut stream =
        TcpStream::connect_timeout(&address, Duration::from_secs(2)).map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|e| e.to_string())?;
    let body = serde_json::json!({"enabled":enabled}).to_string();
    let request = format!("POST /api/system/update-maintenance HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Manager-Token: {token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut response = String::new();
    stream
        .take(100_000)
        .read_to_string(&mut response)
        .map_err(|e| e.to_string())?;
    if !response.starts_with("HTTP/1.0 200 ") && !response.starts_with("HTTP/1.1 200 ") {
        return Err("Wait for active jobs and profile cleanup to finish, then retry. If this persists, restart the app before updating.".into());
    }
    Ok(())
}
struct MaintenanceLease;
impl Drop for MaintenanceLease {
    fn drop(&mut self) {
        let _ = update_maintenance(false);
    }
}
impl AppUpdates {
    fn begin(&self) -> Result<Busy<'_>, String> {
        self.busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "Another update operation is in progress.".to_string())?;
        Ok(Busy(&self.busy))
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    current_version: String,
    version: Option<String>,
    notes: Option<String>,
    downloaded: bool,
    notice: Option<String>,
}

fn release_check_notice(error: &tauri_plugin_updater::Error) -> Option<&'static str> {
    match error {
        tauri_plugin_updater::Error::ReleaseNotFound => Some(
            "Published update information is unavailable. There may not be a published release yet. Please try again later.",
        ),
        _ => None,
    }
}

pub fn ensure_desktop_owned() -> Result<(), String> {
    if std::env::var("WORKER_SERVICE_ATTACHED").as_deref() == Ok("1") {
        return Err(
            "Stop the independently managed worker service and reopen the app before updating."
                .into(),
        );
    }
    Ok(())
}

/// A release version as typed ("2.2.9" or "v2.2.9"), or None when it is not one.
fn release_version(text: &str) -> Option<String> {
    let version = text.trim().trim_start_matches('v');
    let parts: Vec<&str> = version.split('.').collect();
    (parts.len() == 3 && parts.iter().all(|p| !p.is_empty() && p.len() <= 6 && p.bytes().all(|c| c.is_ascii_digit())))
        .then(|| version.to_string())
}

/// Each published release keeps its own signed update manifest, so an earlier version can be
/// installed with the same signature check as the latest one.
fn release_manifest(version: &str) -> String {
    format!("https://github.com/rathana-pvs/anti_bot_browser/releases/download/v{version}/latest.json")
}

#[tauri::command]
pub async fn check_app_update(
    app: AppHandle,
    state: State<'_, AppUpdates>,
    version: Option<String>,
) -> Result<UpdateInfo, String> {
    let _busy = state.begin()?;
    // A named version is offered whether it is older or newer than this one, to go back to it.
    let wanted = match version.as_deref().map(str::trim).filter(|v| !v.is_empty()) {
        Some(text) => Some(release_version(text).ok_or("Enter a version such as 2.2.9.")?),
        None => None,
    };
    if wanted.as_deref() == Some(app.package_info().version.to_string().as_str()) {
        return Err("That version is already installed.".into());
    }
    {
        let mut pending = state
            .pending
            .lock()
            .map_err(|_| "Update state is unavailable")?;
        *pending = Pending::default();
    }
    let mut builder = app.updater_builder().timeout(Duration::from_secs(20));
    if let Some(ref wanted) = wanted {
        let manifest = tauri::Url::parse(&release_manifest(wanted)).map_err(|e| e.to_string())?;
        let target = wanted.clone();
        builder = builder
            .endpoints(vec![manifest])
            .map_err(|e| e.to_string())?
            .version_comparator(move |current, remote| {
                remote.version.to_string() == target && remote.version != current
            });
    }
    let result = builder.build().map_err(|e| e.to_string())?.check().await;
    let (update, notice) = match result {
        Ok(update) => (update, None),
        Err(tauri_plugin_updater::Error::ReleaseNotFound) if wanted.is_some() => {
            return Err(format!("Version {} was not found among the published releases.", wanted.unwrap_or_default()))
        }
        Err(error) => match release_check_notice(&error) {
            Some(notice) => (None, Some(notice.to_string())),
            None => return Err(format!("Could not check for updates. Check your connection and try again. Details: {error}")),
        },
    };
    if let Some(ref update) = update {
        if update.download_url.scheme() != "https" {
            return Err("Update download must use HTTPS.".into());
        }
    }
    let info = UpdateInfo {
        current_version: app.package_info().version.to_string(),
        version: update.as_ref().map(|u| u.version.clone()),
        notes: update.as_ref().and_then(|u| u.body.clone()),
        downloaded: false,
        notice,
    };
    state
        .pending
        .lock()
        .map_err(|_| "Update state is unavailable")?
        .update = update;
    Ok(info)
}

#[tauri::command]
pub async fn download_app_update(
    app: AppHandle,
    state: State<'_, AppUpdates>,
) -> Result<(), String> {
    let _busy = state.begin()?;
    let mut update = state
        .pending
        .lock()
        .map_err(|_| "Update state is unavailable")?
        .update
        .clone()
        .ok_or("Check for updates first.")?;
    update.timeout = Some(Duration::from_secs(300));
    let mut downloaded = 0u64;
    let bytes = update
        .download(
            |chunk, total| {
                downloaded += chunk as u64;
                let _ = app.emit(
                    "app-update-progress",
                    serde_json::json!({"downloaded":downloaded,"total":total}),
                );
            },
            || {},
        )
        .await
        .map_err(|e| format!("Download or signature verification failed: {e}"))?;
    state
        .pending
        .lock()
        .map_err(|_| "Update state is unavailable")?
        .bytes = Some(Arc::new(bytes));
    Ok(())
}

#[tauri::command]
pub async fn install_app_update(
    app: AppHandle,
    state: State<'_, AppUpdates>,
) -> Result<(), String> {
    let _busy = state.begin()?;
    ensure_desktop_owned()?;
    // The same exclusion flag is used by runtime setup/repair.
    let setup = app.state::<SetupProcess>();
    {
        let mut running = setup.0.lock().map_err(|_| "Setup state is unavailable")?;
        if *running {
            return Err("Wait for setup or another installation to finish.".into());
        }
        *running = true;
    }
    let _setup_lease = SetupLease(&setup.0);
    let data = {
        let pending = state
            .pending
            .lock()
            .map_err(|_| "Update state is unavailable")?;
        pending.update.clone().zip(pending.bytes.clone())
    };
    let Some((update, bytes)) = data else {
        return Err("Download and verify the update first.".into());
    };
    let install_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        update.verify(bytes.as_ref()).map_err(|e| e.to_string())?;
        // With application services stopped (as on the setup screen) no automation can be running,
        // so the update installs straight away: a failed repair must not block the release that fixes it.
        let _maintenance = MaintenanceLease;
        update_maintenance(true)?;
        verify_runtime_install_idle()?;
        let backend = install_app.state::<BackendProcess>();
        let child = {
            let mut owned = backend
                .0
                .lock()
                .map_err(|_| "Backend state is unavailable")?;
            if backend_is_running() && owned.is_none() {
                return Err(
                    "The running backend is not owned by this desktop. Stop it before updating."
                        .into(),
                );
            }
            owned.take()
        };
        let stopped_backend = child.is_some();
        if let Some(mut child) = child {
            terminate_backend(&mut child);
        }
        if backend_is_running() {
            return Err("Application services have not stopped; update cancelled.".into());
        }
        let outcome = update
            .install(bytes.as_ref())
            .map_err(|e| format!("Installation failed: {e}"));
        if outcome.is_err() && stopped_backend {
            if let Ok(mut owned) = backend.0.lock() {
                *owned = spawn_backend(&install_app);
            }
        }
        outcome
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|r| r);
    result?;
    // Linux installers return after installation.
    app.restart();
}

#[tauri::command]
pub fn app_update_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unavailable_release_is_informational_but_other_errors_remain_errors() {
        assert!(release_check_notice(&tauri_plugin_updater::Error::ReleaseNotFound)
            .unwrap().contains("may not be a published release"));
        assert!(release_check_notice(&tauri_plugin_updater::Error::EmptyEndpoints).is_none());
        let invalid_json = serde_json::from_str::<serde_json::Value>("not json").unwrap_err();
        assert!(release_check_notice(&tauri_plugin_updater::Error::Serialization(invalid_json)).is_none());
    }
    #[test]
    fn only_a_release_version_names_an_earlier_release() {
        assert_eq!(release_version(" v2.2.9 ").as_deref(), Some("2.2.9"));
        assert_eq!(release_version("2.2.10").as_deref(), Some("2.2.10"));
        for bad in ["", "2.2", "2.2.9.1", "2.2.x", "../2.2.9", "2.2.9/evil"] {
            assert!(release_version(bad).is_none(), "{bad}");
        }
        assert_eq!(release_manifest("2.2.9"),
            "https://github.com/rathana-pvs/anti_bot_browser/releases/download/v2.2.9/latest.json");
    }
    #[test]
    fn operations_are_exclusive_and_guard_releases_on_error() {
        let state = AppUpdates::default();
        let guard = state.begin().unwrap();
        assert!(state.begin().is_err());
        drop(guard);
        assert!(state.begin().is_ok());
    }
    #[test]
    fn installation_exclusion_is_released_on_early_return() {
        let flag = Mutex::new(true);
        {
            let _lease = SetupLease(&flag);
        }
        assert!(!*flag.lock().unwrap());
    }
}
