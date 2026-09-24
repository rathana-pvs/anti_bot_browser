// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::Command;

#[tauri::command]
fn execute_profile_action(profile_id: String, action: String) -> Result<String, String> {
    let output = Command::new("bash")
        .args(["../scripts/run_profile.sh", &profile_id, &action])
        .output()
        .map_err(|e| e.to_string())?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![execute_profile_action])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
