// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(target_os = "windows"))]
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;
use tauri::{AppHandle, Manager};

struct BackendProcess(Mutex<Option<Child>>);

fn backend_is_running() -> bool {
    let address: SocketAddr = "127.0.0.1:8000".parse().expect("valid backend address");
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

#[cfg(target_os = "windows")]
fn decode_windows_text(bytes: &[u8]) -> String {
    if bytes.len() >= 2 && bytes[1] == 0 {
        let words: Vec<u16> = bytes
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        String::from_utf16_lossy(&words)
    } else {
        String::from_utf8_lossy(bytes).into_owned()
    }
}

#[cfg(target_os = "windows")]
fn find_ubuntu_distribution() -> Option<String> {
    use std::os::windows::process::CommandExt;
    let output = Command::new("wsl.exe")
        .args(["--list", "--quiet"])
        .creation_flags(0x08000000)
        .output()
        .ok()?;
    decode_windows_text(&output.stdout)
        .lines()
        .map(|line| line.trim_matches(|character: char| character == '\0' || character.is_whitespace()))
        .find(|line| line.to_ascii_lowercase().contains("ubuntu"))
        .map(str::to_owned)
}

#[cfg(target_os = "windows")]
fn spawn_wsl_backend() -> Option<Child> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;

    if backend_is_running() {
        println!("[Tauri] Reusing backend already listening on 127.0.0.1:8000");
        return None;
    }

    let distribution = find_ubuntu_distribution()?;
    let launch = concat!(
        "cd \"$HOME/automat_fb-beta\" && ",
        "PYTHONPATH=. exec automation/venv/bin/uvicorn backend.main:app ",
        "--host 127.0.0.1 --port 8000"
    );
    let mut command = Command::new("wsl.exe");
    command
        .args(["-d", &distribution, "--", "bash", "-lc", launch])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW);

    match command.spawn() {
        Ok(child) => {
            println!("[Tauri] Starting backend in WSL distribution {distribution}");
            Some(child)
        }
        Err(error) => {
            eprintln!("[Tauri] Failed to start WSL backend: {error}");
            None
        }
    }
}

#[cfg(not(target_os = "windows"))]
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

#[allow(unused_variables)]
fn spawn_backend(app: &AppHandle) -> Option<Child> {
    #[cfg(target_os = "windows")]
    {
        return spawn_wsl_backend();
    }

    #[cfg(not(target_os = "windows"))]
    if let Some(binary_path) = find_backend_binary(app) {
        println!("[Tauri] Starting backend server from {:?}", binary_path);
        let mut cmd = Command::new(&binary_path);

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
