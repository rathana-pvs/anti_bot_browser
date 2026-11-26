//! The floating status window: a small, always-on-top second window of this same app.
//!
//! It reads the same local backend as the main window. While it is open, closing the main
//! window only hides it, so posting keeps going; closing the mini window brings the main
//! window back (its status panel docks again), so the app is never left with no window.

use serde_json::Value;
use tauri::{AppHandle, Emitter, LogicalSize, Manager, WebviewUrl, WebviewWindowBuilder, Window, WindowEvent};

pub const MAIN_LABEL: &str = "main";
pub const MINI_LABEL: &str = "mini";

const MINI_WIDTH: f64 = 340.0;
const MINI_MIN_HEIGHT: f64 = 44.0;
const MINI_MAX_HEIGHT: f64 = 600.0;

fn show_main(app: &AppHandle) {
    if let Some(main) = app.get_webview_window(MAIN_LABEL) {
        let _ = main.show();
        let _ = main.unminimize();
        let _ = main.set_focus();
    }
}

/// Opens the mini window, or brings it forward when it is already open.
#[tauri::command]
pub fn open_mini_window(app: AppHandle) -> Result<(), String> {
    if let Some(mini) = app.get_webview_window(MINI_LABEL) {
        let _ = mini.show();
        let _ = mini.unminimize();
        let _ = mini.set_focus();
        return Ok(());
    }
    let window = WebviewWindowBuilder::new(&app, MINI_LABEL, WebviewUrl::App("index.html".into()))
        .title("Worker status")
        .inner_size(MINI_WIDTH, 360.0)
        .min_inner_size(280.0, MINI_MIN_HEIGHT)
        .decorations(false)
        .always_on_top(true)
        .resizable(true)
        .build()
        .map_err(|error| format!("Could not open the status window: {error}"))?;
    // Top-right corner of the screen it opens on, clear of the edge.
    if let Ok(Some(monitor)) = window.current_monitor() {
        let scale = monitor.scale_factor();
        let size = monitor.size().to_logical::<f64>(scale);
        let origin = monitor.position().to_logical::<f64>(scale);
        let _ = window.set_position(tauri::LogicalPosition::new(origin.x + size.width - MINI_WIDTH - 24.0, origin.y + 48.0));
    }
    Ok(())
}

/// Shows the main window, optionally at a page the mini window asked for
/// (`{"page": "queue", "executionId": ...}` or `{"page": "live", "profileId": ...}`).
#[tauri::command]
pub fn show_main_window(app: AppHandle, target: Option<Value>) {
    show_main(&app);
    if let Some(target) = target {
        let _ = app.emit_to(MAIN_LABEL, "mini-navigate", target);
    }
}

/// Closes the mini window; its close handler shows the main window and docks the panel there.
#[tauri::command]
pub fn dock_mini_window(app: AppHandle) {
    if let Some(mini) = app.get_webview_window(MINI_LABEL) {
        let _ = mini.close();
    }
}

#[tauri::command]
pub fn set_mini_on_top(app: AppHandle, on_top: bool) -> Result<(), String> {
    let mini = app.get_webview_window(MINI_LABEL).ok_or("The status window is not open")?;
    mini.set_always_on_top(on_top).map_err(|error| error.to_string())
}

/// Fits the window to its content, so a collapsed panel is only its title bar.
#[tauri::command]
pub fn set_mini_height(app: AppHandle, height: f64) -> Result<(), String> {
    let mini = app.get_webview_window(MINI_LABEL).ok_or("The status window is not open")?;
    let width = mini
        .inner_size()
        .ok()
        .zip(mini.scale_factor().ok())
        .map(|(size, scale)| size.to_logical::<f64>(scale).width)
        .unwrap_or(MINI_WIDTH);
    let height = height.clamp(MINI_MIN_HEIGHT, MINI_MAX_HEIGHT).round();
    mini.set_size(LogicalSize::new(width, height)).map_err(|error| error.to_string())
}

/// Other windows of the app still open (the mini window, live windows), leaving out `except`.
fn other_windows_open(app: &AppHandle, except: &str) -> bool {
    app.webview_windows().keys().any(|label| label != MAIN_LABEL && label != except)
}

/// Window events for every window. Returns true when the event was fully handled here.
pub fn handle_window_event(window: &Window, event: &WindowEvent) -> bool {
    let app = window.app_handle();
    let label = window.label();
    match event {
        // While another window is open, the main window hides instead of closing; work goes on.
        WindowEvent::CloseRequested { api, .. } if label == MAIN_LABEL && other_windows_open(app, MAIN_LABEL) => {
            api.prevent_close();
            let _ = window.hide();
            true
        }
        WindowEvent::Destroyed if label == MINI_LABEL => {
            show_main(app);
            let _ = app.emit_to(MAIN_LABEL, "mini-docked", ());
            true
        }
        WindowEvent::Destroyed if label.starts_with(crate::live_window::LIVE_PREFIX) => {
            if let Some(profile_id) = crate::live_window::profile_of(label) {
                let _ = app.emit_to(MAIN_LABEL, "live-window-closed", serde_json::json!({ "profileId": profile_id }));
            }
            // The last extra window closing never leaves the app running with nothing on screen.
            if !other_windows_open(app, label) {
                show_main(app);
            }
            true
        }
        _ => false,
    }
}
