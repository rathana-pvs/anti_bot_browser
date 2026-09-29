// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};

struct BackendProcess(Mutex<Option<Child>>);
struct SetupProcess(Mutex<bool>);

#[cfg(target_os = "windows")]
fn suppress_console_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(target_os = "windows"))]
fn suppress_console_window(_command: &mut Command) {}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct SetupStep {
    id: String,
    title: String,
    description: String,
    status: String,
    detail: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SetupSnapshot {
    platform: String,
    ready: bool,
    restart_required: bool,
    steps: Vec<SetupStep>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SetupRunResult {
    success: bool,
    exit_code: i32,
    restart_required: bool,
    message: String,
}

fn command_succeeds(program: &str, args: &[&str]) -> bool {
    let mut command = Command::new(program);
    suppress_console_window(&mut command);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(not(target_os = "windows"))]
fn container_platform_installed() -> bool {
    command_succeeds("which", &["docker"])
}

#[cfg(target_os = "windows")]
fn container_platform_installed() -> bool {
    let desktop = std::env::var_os("ProgramFiles")
        .map(PathBuf::from)
        .map(|path| path.join("Docker/Docker/Docker Desktop.exe"))
        .map(|path| path.exists())
        .unwrap_or(false);
    desktop
        || find_ubuntu_distribution()
            .map(|name| command_succeeds("wsl.exe", &["-d", &name, "--", "docker", "--version"]))
            .unwrap_or(false)
}

fn find_install_root(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(current) = std::env::current_dir() {
        candidates.push(current.clone());
        if current.ends_with("manager-app") {
            if let Some(parent) = current.parent() {
                candidates.push(parent.to_path_buf());
            }
        }
    }
    if let Ok(executable) = std::env::current_exe() {
        let mut current = executable.parent();
        for _ in 0..5 {
            if let Some(path) = current {
                candidates.push(path.to_path_buf());
                current = path.parent();
            }
        }
    }
    if let Ok(app_data_dir) = app.path().app_data_dir() {
        candidates.push(app_data_dir.join("runtime"));
    }
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.clone());
        candidates.push(resource_dir.join("payload"));
    }
    candidates
        .into_iter()
        .find(|path| path.join("install.sh").exists() || path.join("install-windows.ps1").exists())
}

#[cfg(not(target_os = "windows"))]
fn copy_setup_payload(
    source: &std::path::Path,
    destination: &std::path::Path,
) -> Result<(), String> {
    std::fs::create_dir_all(destination)
        .map_err(|error| format!("Could not create the application runtime directory: {error}"))?;
    for entry in std::fs::read_dir(source)
        .map_err(|error| format!("Could not read the setup payload: {error}"))?
    {
        let entry =
            entry.map_err(|error| format!("Could not read a setup payload entry: {error}"))?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        if source_path.is_dir() {
            copy_setup_payload(&source_path, &destination_path)?;
        } else {
            std::fs::copy(&source_path, &destination_path)
                .map_err(|error| format!("Could not copy {}: {error}", source_path.display()))?;
        }
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn prepare_install_root(app: &AppHandle) -> Result<PathBuf, String> {
    let bundled_payload = app
        .path()
        .resource_dir()
        .map(|path| path.join("payload"))
        .ok()
        .filter(|path| path.join("install.sh").exists());
    if let Some(source) = bundled_payload {
        let runtime = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("Could not locate the application data directory: {error}"))?
            .join("runtime");
        copy_setup_payload(&source, &runtime)?;
        Ok(runtime)
    } else {
        find_install_root(app).ok_or_else(|| {
            "Installation payload was not found. Reinstall from the complete release package."
                .to_string()
        })
    }
}

#[cfg(target_os = "windows")]
fn prepare_install_root(app: &AppHandle) -> Result<PathBuf, String> {
    find_install_root(app).ok_or_else(|| {
        "Installation payload was not found. Reinstall from the complete release package."
            .to_string()
    })
}

fn backend_is_running() -> bool {
    let address: SocketAddr = "127.0.0.1:3001".parse().expect("valid backend address");
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
        .map(|line| {
            line.trim_matches(|character: char| character == '\0' || character.is_whitespace())
        })
        .find(|line| line.to_ascii_lowercase().contains("ubuntu"))
        .map(str::to_owned)
}

#[cfg(target_os = "windows")]
fn setup_state(root: Option<&PathBuf>) -> (bool, bool, bool, bool, bool) {
    let _ = root;
    let system_ready = command_succeeds("wsl.exe", &["--status"]);
    let distribution = find_ubuntu_distribution();
    let platform_ready = distribution
        .as_ref()
        .map(|name| command_succeeds("wsl.exe", &["-d", name, "--", "docker", "info"]))
        .unwrap_or(false);
    let image_ready = distribution
        .as_ref()
        .map(|name| {
            command_succeeds(
                "wsl.exe",
                &[
                    "-d",
                    name,
                    "--",
                    "docker",
                    "image",
                    "inspect",
                    "isolated-chrome:latest",
                ],
            )
        })
        .unwrap_or(false);
    let runtime_ready = distribution
        .as_ref()
        .map(|name| {
            command_succeeds(
                "wsl.exe",
                &[
                    "-d",
                    name,
                    "--",
                    "bash",
                    "-lc",
                    "test -x \"$HOME/automat_fb-beta/automation/venv/bin/python\"",
                ],
            )
        })
        .unwrap_or(false);
    let configuration_ready = distribution
        .as_ref()
        .map(|name| command_succeeds("wsl.exe", &["-d", name, "--", "bash", "-lc", "test -f \"$HOME/automat_fb-beta/data/manager_settings.json\" && test -f \"$HOME/automat_fb-beta/proxies/proxy_pool.json\""]))
        .unwrap_or(false);
    (
        system_ready,
        platform_ready,
        runtime_ready,
        image_ready,
        configuration_ready,
    )
}

#[cfg(not(target_os = "windows"))]
fn setup_state(root: Option<&PathBuf>) -> (bool, bool, bool, bool, bool) {
    let system_ready = std::fs::read_to_string("/etc/os-release")
        .map(|contents| contents.contains("ID=ubuntu") || contents.contains("ID=debian"))
        .unwrap_or(false);
    let platform_ready = command_succeeds("docker", &["info"]);
    let image_ready = platform_ready
        && command_succeeds("docker", &["image", "inspect", "isolated-chrome:latest"]);
    let runtime_ready = root
        .map(|path| path.join("automation/venv/bin/python").exists())
        .unwrap_or(false);
    let configuration_ready = root
        .map(|path| {
            path.join("data/manager_settings.json").exists()
                && path.join("proxies/proxy_pool.json").exists()
        })
        .unwrap_or(false);
    (
        system_ready,
        platform_ready,
        runtime_ready,
        image_ready,
        configuration_ready,
    )
}

#[tauri::command]
fn get_setup_status(app: AppHandle) -> SetupSnapshot {
    let root = find_install_root(&app);
    let (system_ready, platform_ready, runtime_ready, image_ready, configuration_ready) =
        setup_state(root.as_ref());
    let verification_ready = backend_is_running() && platform_ready && image_ready;
    let platform_installed = container_platform_installed();
    let status = |ready: bool, waiting: bool| {
        if ready {
            "ready"
        } else if waiting {
            "waiting"
        } else {
            "action_required"
        }
    };
    let steps = vec![
        SetupStep {
            id: "system".into(),
            title: "System check".into(),
            description: "Operating system and required platform features".into(),
            status: status(system_ready, false).into(),
            detail: if system_ready {
                "Supported system detected"
            } else {
                "System setup is required"
            }
            .into(),
        },
        SetupStep {
            id: "platform".into(),
            title: if cfg!(target_os = "windows") {
                "WSL and Docker".into()
            } else {
                "Docker Engine".into()
            },
            description: "Isolated container platform for browser profiles".into(),
            status: status(platform_ready, !system_ready).into(),
            detail: if platform_ready {
                "Container engine is running"
            } else if platform_installed {
                "Docker is installed but is not running"
            } else {
                "Install and configure the container platform"
            }
            .into(),
        },
        SetupStep {
            id: "runtime".into(),
            title: "Application runtime".into(),
            description: "Private automation runtime and application services".into(),
            status: status(runtime_ready, !platform_ready).into(),
            detail: if runtime_ready {
                "Automation runtime is installed"
            } else {
                "Runtime installation is required"
            }
            .into(),
        },
        SetupStep {
            id: "browser".into(),
            title: "Browser environment".into(),
            description: "Isolated Chrome image and visual automation tools".into(),
            status: status(image_ready, !platform_ready).into(),
            detail: if image_ready {
                "Browser image is available"
            } else {
                "Browser image must be downloaded or built"
            }
            .into(),
        },
        SetupStep {
            id: "configuration".into(),
            title: "Recommended configuration".into(),
            description: "Safe resource, queue, storage, and proxy defaults".into(),
            status: status(configuration_ready, !runtime_ready).into(),
            detail: if configuration_ready {
                "Recommended defaults are applied"
            } else {
                "Default configuration must be created"
            }
            .into(),
        },
        SetupStep {
            id: "verification".into(),
            title: "Final verification".into(),
            description: "Backend, Docker, browser image, and local connectivity".into(),
            status: status(verification_ready, !(runtime_ready && image_ready)).into(),
            detail: if verification_ready {
                "All core services are ready"
            } else {
                "Final system test is pending"
            }
            .into(),
        },
    ];
    let ready = steps.iter().all(|step| step.status == "ready");
    SetupSnapshot {
        platform: if cfg!(target_os = "windows") {
            "windows".into()
        } else {
            "linux".into()
        },
        ready,
        restart_required: false,
        steps,
    }
}

fn emit_setup_line(app: &AppHandle, stream: &str, line: &str) {
    let _ = app.emit(
        "setup-log",
        serde_json::json!({ "stream": stream, "message": line }),
    );
}

#[tauri::command]
fn get_setup_log(app: AppHandle) -> Vec<String> {
    let Some(root) = find_install_root(&app) else {
        return Vec::new();
    };
    let Ok(contents) = std::fs::read_to_string(root.join("data/install.log")) else {
        return Vec::new();
    };
    let mut lines: Vec<String> = contents
        .lines()
        .rev()
        .take(500)
        .map(str::to_owned)
        .collect();
    lines.reverse();
    lines
}

#[tauri::command]
async fn run_setup(
    app: AppHandle,
    state: tauri::State<'_, SetupProcess>,
    backend_state: tauri::State<'_, BackendProcess>,
    repair: bool,
) -> Result<SetupRunResult, String> {
    {
        let mut running = state
            .0
            .lock()
            .map_err(|_| "Setup state is unavailable".to_string())?;
        if *running {
            return Err("Setup is already running".into());
        }
        *running = true;
    }
    let result = run_setup_process(&app, repair);
    if result.as_ref().map(|value| value.success).unwrap_or(false) && !backend_is_running() {
        if let Some(child) = spawn_backend(&app) {
            if let Ok(mut backend) = backend_state.0.lock() {
                *backend = Some(child);
            }
        }
    }
    if let Ok(mut running) = state.0.lock() {
        *running = false;
    }
    result
}

#[cfg(not(target_os = "windows"))]
fn start_container_platform(app: &AppHandle) -> Result<(), String> {
    if !container_platform_installed() {
        return Err("Docker is not installed. Run installation and repair instead.".into());
    }
    emit_setup_line(app, "system", "Starting Docker service…");
    let status = Command::new("pkexec")
        .args(["systemctl", "start", "docker"])
        .status()
        .map_err(|error| format!("Could not request Docker startup: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(
            "Docker service did not start. Check the activity log and system service status."
                .into(),
        )
    }
}

#[cfg(target_os = "windows")]
fn start_container_platform(app: &AppHandle) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    let desktop = std::env::var_os("ProgramFiles")
        .map(PathBuf::from)
        .map(|path| path.join("Docker/Docker/Docker Desktop.exe"))
        .filter(|path| path.exists())
        .ok_or_else(|| {
            "Docker Desktop is not installed. Run installation and repair instead.".to_string()
        })?;
    emit_setup_line(app, "system", "Starting Docker Desktop…");
    Command::new(desktop)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| format!("Could not start Docker Desktop: {error}"))?;
    Ok(())
}

#[tauri::command]
async fn recover_services(
    app: AppHandle,
    backend_state: tauri::State<'_, BackendProcess>,
) -> Result<SetupRunResult, String> {
    let (_, platform_ready, _, _, _) = setup_state(find_install_root(&app).as_ref());
    if !platform_ready {
        start_container_platform(&app)?;
        for attempt in 1..=30 {
            let (_, ready, _, _, _) = setup_state(find_install_root(&app).as_ref());
            if ready {
                emit_setup_line(&app, "system", "Docker is ready.");
                break;
            }
            if attempt % 5 == 0 {
                emit_setup_line(&app, "system", &format!("Waiting for Docker… {attempt}/30"));
            }
            std::thread::sleep(Duration::from_secs(2));
        }
    }
    let (_, platform_ready, _, _, _) = setup_state(find_install_root(&app).as_ref());
    if platform_ready && !backend_is_running() {
        emit_setup_line(&app, "system", "Starting application services…");
        if let Some(child) = spawn_backend(&app) {
            if let Ok(mut backend) = backend_state.0.lock() {
                *backend = Some(child);
            }
        }
        for _ in 0..15 {
            if backend_is_running() {
                break;
            }
            std::thread::sleep(Duration::from_secs(1));
        }
    }
    let success = platform_ready && backend_is_running();
    Ok(SetupRunResult {
        success,
        exit_code: if success { 0 } else { 1 },
        restart_required: false,
        message: if success {
            "Required services are running".into()
        } else {
            "Services are still starting. Wait a moment, then check again.".into()
        },
    })
}

fn run_setup_process(app: &AppHandle, repair: bool) -> Result<SetupRunResult, String> {
    let root = prepare_install_root(app)?;
    let mode = if repair { "repair" } else { "install" };
    emit_setup_line(
        app,
        "system",
        &format!("Starting {mode} from {}", root.display()),
    );

    #[cfg(target_os = "windows")]
    let mut command = {
        let script = root.join("install-windows.ps1");
        let mut command = Command::new("powershell.exe");
        suppress_console_window(&mut command);
        command.args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script.to_string_lossy().as_ref(),
            "-Mode",
            mode,
            "-NonInteractive",
        ]);
        command
    };

    #[cfg(not(target_os = "windows"))]
    let mut command = {
        let script = root.join("install.sh");
        let mode_arg = format!("--{mode}");
        let mut command = Command::new("bash");
        command.args([
            script.to_string_lossy().as_ref(),
            &mode_arg,
            "--non-interactive",
            "--desktop",
        ]);
        command
    };

    let mut child = command
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Could not start setup: {error}"))?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let stdout_app = app.clone();
    let stdout_thread = std::thread::spawn(move || {
        if let Some(stdout) = stdout {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                emit_setup_line(&stdout_app, "stdout", &line);
            }
        }
    });
    let stderr_app = app.clone();
    let stderr_thread = std::thread::spawn(move || {
        if let Some(stderr) = stderr {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                emit_setup_line(&stderr_app, "stderr", &line);
            }
        }
    });
    let exit = child
        .wait()
        .map_err(|error| format!("Setup process failed: {error}"))?;
    let _ = stdout_thread.join();
    let _ = stderr_thread.join();
    let code = exit.code().unwrap_or(-1);
    let restart_required = matches!(code, 10 | 11 | 12);
    Ok(SetupRunResult {
        success: exit.success(),
        exit_code: code,
        restart_required,
        message: if exit.success() {
            "Setup completed successfully".into()
        } else if restart_required {
            "A restart or external action is required before setup can continue".into()
        } else {
            format!("Setup stopped with exit code {code}")
        },
    })
}

#[cfg(target_os = "windows")]
fn spawn_wsl_backend() -> Option<Child> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;

    if backend_is_running() {
        println!("[Tauri] Reusing backend already listening on 127.0.0.1:3001");
        return None;
    }

    let distribution = find_ubuntu_distribution()?;
    let launch = concat!(
        "cd \"$HOME/automat_fb-beta\" && ",
        "PYTHONPATH=. exec automation/venv/bin/uvicorn backend.main:app ",
        "--host 127.0.0.1 --port 3001"
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
    let cwd_src_tauri = PathBuf::from("src-tauri")
        .join("binaries")
        .join(binary_name);
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
                println!(
                    "[Tauri] Backend server started successfully (pid: {})",
                    child.id()
                );
                Some(child)
            }
            Err(e) => {
                eprintln!("[Tauri] Failed to spawn backend binary: {}", e);
                None
            }
        }
    } else {
        if let Some(root) = find_install_root(app) {
            let uvicorn = root.join("automation/venv/bin/uvicorn");
            if uvicorn.exists() {
                println!("[Tauri] Starting backend from the private application runtime");
                return Command::new(uvicorn)
                    .args(["backend.main:app", "--host", "127.0.0.1", "--port", "3001"])
                    .current_dir(&root)
                    .env("PYTHONPATH", &root)
                    .spawn()
                    .ok();
            }
        }
        println!("[Tauri] No backend runtime was found. Setup is required.");
        None
    }
}

fn main() {
    tauri::Builder::default()
        .manage(SetupProcess(Mutex::new(false)))
        .invoke_handler(tauri::generate_handler![
            get_setup_status,
            get_setup_log,
            run_setup,
            recover_services
        ])
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
