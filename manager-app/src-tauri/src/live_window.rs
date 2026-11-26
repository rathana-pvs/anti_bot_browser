//! Pop-out live viewers: one window per account (label `live-<profile id>`) showing that
//! account's browser (PC) or phone screen. Closing one only closes the view; the browser or
//! phone keeps working. Only one viewer per account exists at a time: while an account's
//! window is open, the main window shows a placeholder instead of a second stream.

use serde_json::json;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::mini_window::MAIN_LABEL;

pub const LIVE_PREFIX: &str = "live-";

/// Profile ids become window labels, which allow only a few characters.
fn valid_profile_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 96 && id.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
}

pub fn profile_of(label: &str) -> Option<&str> {
    label.strip_prefix(LIVE_PREFIX).filter(|id| valid_profile_id(id))
}

/// Opens an account's live window, or brings it forward when it is already open.
#[tauri::command]
pub fn open_live_window(app: AppHandle, profile_id: String, title: String, phone: bool) -> Result<(), String> {
    if !valid_profile_id(&profile_id) {
        return Err("That account id can't be used for a window".into());
    }
    let label = format!("{LIVE_PREFIX}{profile_id}");
    if let Some(existing) = app.get_webview_window(&label) {
        let _ = existing.show();
        let _ = existing.unminimize();
        let _ = existing.set_focus();
        return Ok(());
    }
    let (width, height, min_width, min_height) = if phone { (420.0, 860.0, 260.0, 420.0) } else { (1100.0, 760.0, 480.0, 360.0) };
    let title: String = title.chars().filter(|ch| !ch.is_control()).take(80).collect();
    WebviewWindowBuilder::new(&app, &label, WebviewUrl::App("index.html".into()))
        .title(if title.is_empty() { "Live view".to_string() } else { format!("{title} · live") })
        .inner_size(width, height)
        .min_inner_size(min_width, min_height)
        .resizable(true)
        .build()
        .map_err(|error| format!("Could not open the live window: {error}"))?;
    let _ = app.emit_to(MAIN_LABEL, "live-window-opened", json!({ "profileId": profile_id }));
    Ok(())
}

/// Accounts whose live window is open now, so a reloaded main window knows which to leave out.
#[tauri::command]
pub fn list_live_windows(app: AppHandle) -> Vec<String> {
    app.webview_windows().keys().filter_map(|label| profile_of(label).map(str::to_string)).collect()
}

/// Keeps the calling window above other apps, or stops doing so.
#[tauri::command]
pub fn set_window_on_top(window: WebviewWindow, on_top: bool) -> Result<(), String> {
    window.set_always_on_top(on_top).map_err(|error| error.to_string())
}

/// Closes the calling live window and shows the account's screen in the main window again.
#[tauri::command]
pub fn dock_live_window(app: AppHandle, window: WebviewWindow) {
    let Some(profile_id) = profile_of(window.label()).map(str::to_string) else { return };
    let _ = window.close();
    if let Some(main) = app.get_webview_window(MAIN_LABEL) {
        let _ = main.show();
        let _ = main.unminimize();
        let _ = main.set_focus();
    }
    let _ = app.emit_to(MAIN_LABEL, "mini-navigate", json!({ "page": "live", "profileId": profile_id }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_labels_carry_only_safe_account_ids() {
        assert_eq!(profile_of("live-profile_005"), Some("profile_005"));
        assert_eq!(profile_of("live-"), None);
        assert_eq!(profile_of("live-../etc"), None);
        assert_eq!(profile_of("live-a b"), None);
        assert_eq!(profile_of("mini"), None);
        assert_eq!(profile_of("main"), None);
    }
}
