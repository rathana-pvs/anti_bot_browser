// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{AppHandle, Manager};

struct BackendProcess(Mutex<Option<Child>>);

fn find_backend_binary(app: &AppHandle) -> Option<PathBuf> {
    let binary_name = if cfg!(target_os = "windows") {
        "backend_server.exe"
    } else {
        "backend_server"
    };

    // 1. Check in same folder as current executable
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let candidate = exe_dir.join(binary_name);
            if candidate.exists() {
                return Some(candidate);
            }
            let candidate = exe_dir.join("binaries").join(binary_name);
            if candidate.exists() {
                return Some(candidate);
            }
            let candidate = exe_dir.join("resources").join(binary_name);
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }

    // 2. Check in app resource dir (Tauri bundle)
    if let Ok(res_dir) = app.path().resource_dir() {
        let candidate = res_dir.join(binary_name);
        if candidate.exists() {
            return Some(candidate);
        }
        let candidate = res_dir.join("binaries").join(binary_name);
        if candidate.exists() {
            return Some(candidate);
        }
        let candidate = res_dir.join("resources").join(binary_name);
        if candidate.exists() {
            return Some(candidate);
        }
    }

    // 3. Check relative to current working directory
    let cwd_candidate = PathBuf::from("binaries").join(binary_name);
    if cwd_candidate.exists() {
        return Some(cwd_candidate);
    }
    let cwd_src_tauri = PathBuf::from("src-tauri").join("binaries").join(binary_name);
    if cwd_src_tauri.exists() {
        return Some(cwd_src_tauri);
    }

    None
}

fn spawn_backend(app: &AppHandle) -> Option<Child> {
    if let Some(binary_path) = find_backend_binary(app) {
        println!("[Tauri] Starting backend server from {:?}", binary_path);
        let mut cmd = Command::new(&binary_path);

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        match cmd.spawn() {
            Ok(child) => {
                println!("[Tauri] Backend server started successfully (pid: {})", child.id());
                Some(child)
            }
            Err(e) => {
                eprintln!("[Tauri] Failed to spawn backend binary: {}", e);
                None
            }
        }
    } else {
        println!("[Tauri] No standalone backend binary found. Assuming backend is running via external process or dev server.");
        None
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle();
            let child = spawn_backend(handle);
            app.manage(BackendProcess(Mutex::new(child)));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // When main window is destroyed, terminate the backend server process
                if window.label() == "main" {
                    if let Some(state) = window.try_state::<BackendProcess>() {
                        if let Ok(mut lock) = state.0.lock() {
                            if let Some(mut child) = lock.take() {
                                println!("[Tauri] Shutting down backend server...");
                                let _ = child.kill();
                            }
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
