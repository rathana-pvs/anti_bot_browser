//! Desktop media pickers stay outside WebKit's HTML file-chooser lifecycle.
use std::fs::File;
use std::io::Read;
use std::path::PathBuf;
use std::time::Duration;
use serde_json::Value;
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;
use crate::ApiSession;

const MAX_ASSET: u64 = 25 * 1024 * 1024;

fn import_kind(path: Option<PathBuf>, port: u16, token: &str, kind: &str) -> Result<Option<Value>, String> {
    if kind != "audio" && kind != "image" { return Err("Invalid media type.".into()); }
    let Some(path) = path else { return Ok(None); };
    let file = File::open(&path).map_err(|_| "The selected file could not be opened.")?;
    let metadata = file.metadata().map_err(|_| "The selected file could not be read.")?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_ASSET {
        return Err("Choose a file between 1 byte and 25 MB.".into());
    }
    // Only the name is sent, never a native absolute path or directory.
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("media.file");
    let part = reqwest::blocking::multipart::Part::reader_with_length(file.take(metadata.len()), metadata.len())
        .file_name(name.to_string()).mime_str("application/octet-stream").map_err(|_| "Could not prepare the media import.")?;
    // The updater enables rustls without a provider; initialize its existing
    // ring provider before constructing a shared reqwest client.
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
    let client = reqwest::blocking::Client::builder().no_proxy()
        .redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(90)).build().map_err(|_| "Could not prepare the media import.")?;
    let response = client.post(format!("http://127.0.0.1:{port}/api/reel-creator/assets/{kind}"))
        .header("X-Manager-Token", token)
        .multipart(reqwest::blocking::multipart::Form::new().part("file", part)).send()
        .map_err(|_| "The media import could not reach the local renderer. Check the connection and try again.")?;
    let success = response.status().is_success();
    let mut body = Vec::new();
    response.take(65537).read_to_end(&mut body).map_err(|_| "The media import response was interrupted.")?;
    if body.len() > 65536 { return Err("The media import returned an invalid response.".into()); }
    let value: Value = serde_json::from_slice(&body).map_err(|_| "The media import returned an invalid response.")?;
    if !success {
        return Err(value.get("detail").and_then(Value::as_str).unwrap_or("The selected file could not be imported.").to_string());
    }
    if value.get("kind").and_then(Value::as_str) != Some(kind)
        || value.get("id").and_then(Value::as_str).filter(|id| id.len() == 32 && id.bytes().all(|c| c.is_ascii_hexdigit())).is_none() {
        return Err("The media import returned an invalid asset.".into());
    }
    Ok(Some(value))
}

fn import_selection(path: Option<PathBuf>, port: u16, token: &str) -> Result<Option<Value>, String> {
    import_kind(path, port, token, "audio")
}

fn import_images(paths: Option<Vec<PathBuf>>, max_files: u8, port: u16, token: &str) -> Result<Option<Value>, String> {
    if !(1..=5).contains(&max_files) { return Err("Choose between one and five photos.".into()); }
    let Some(paths) = paths else { return Ok(None); };
    if paths.is_empty() { return Ok(None); }
    if paths.len() > max_files as usize { return Err(format!("Choose up to {max_files} photos for this action.")); }
    let (mut assets, mut errors) = (Vec::new(), Vec::new());
    for path in paths {
        let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("Photo").to_string();
        match import_kind(Some(path), port, token, "image") {
            Ok(Some(asset)) => assets.push(asset),
            Err(error) => errors.push(format!("{name}: {error}")),
            _ => {}
        }
    }
    Ok(Some(serde_json::json!({"assets": assets, "errors": errors})))
}

#[tauri::command]
pub async fn pick_reel_images(app: AppHandle, window: tauri::WebviewWindow, session: State<'_, ApiSession>, max_files: u8) -> Result<Option<Value>, String> {
    if !(1..=5).contains(&max_files) { return Err("Choose between one and five photos.".into()); }
    let (send, receive) = tokio::sync::oneshot::channel();
    app.dialog().file().set_parent(&window).set_title("Choose news photos")
        .add_filter("Photos", &["jpg", "jpeg", "jpe", "jfif", "png", "webp", "avif", "gif", "bmp", "tif", "tiff"])
        .pick_files(move |selection| { let _ = send.send(selection); });
    let selection = receive.await.map_err(|_| "The photo picker closed unexpectedly.")?;
    let paths = selection.map(|files| files.into_iter().map(|file| file.into_path().map_err(|_| "Choose local photos.".to_string())).collect::<Result<Vec<_>, _>>()).transpose()?;
    let port = session.port;
    let token = session.token.clone();
    tauri::async_runtime::spawn_blocking(move || import_images(paths, max_files, port, &token))
        .await.map_err(|_| "The photo import stopped unexpectedly.".to_string())?
}

#[tauri::command]
pub async fn pick_reel_audio(app: AppHandle, window: tauri::WebviewWindow, session: State<'_, ApiSession>) -> Result<Option<Value>, String> {
    let (send, receive) = tokio::sync::oneshot::channel();
    app.dialog().file().set_parent(&window).set_title("Choose reel music")
        .add_filter("Audio", &["mp3", "wav", "m4a", "mp4", "ogg", "flac"])
        .pick_file(move |selection| { let _ = send.send(selection); });
    let selection = receive.await.map_err(|_| "The audio picker closed unexpectedly.")?;
    let path = selection.map(|file| file.into_path().map_err(|_| "Choose a local audio file.")).transpose()?;
    let port = session.port;
    let token = session.token.clone();
    tauri::async_runtime::spawn_blocking(move || import_selection(path, port, &token))
        .await.map_err(|_| "The media import stopped unexpectedly.".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;

    #[test]
    fn cancel_never_contacts_the_backend() {
        assert_eq!(import_selection(None, 0, "test-token").unwrap(), None);
    }

    #[test]
    fn selected_audio_streams_to_authenticated_loopback() {
        let path = std::env::temp_dir().join(format!("reel-import-{}.wav", uuid::Uuid::new_v4()));
        std::fs::write(&path, b"example-test-audio").unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let name = path.file_name().unwrap().to_str().unwrap().to_string();
        let server = std::thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
            let mut reader = BufReader::new(stream);
            let mut headers = String::new();
            loop {
                let mut line = String::new(); reader.read_line(&mut line).unwrap();
                headers.push_str(&line); if line == "\r\n" { break; }
            }
            assert!(headers.starts_with("POST /api/reel-creator/assets/audio HTTP/1.1"));
            assert!(headers.to_lowercase().contains("x-manager-token: test-token"));
            let size: usize = headers.lines().find_map(|line| line.to_lowercase().strip_prefix("content-length: ").map(str::to_string)).unwrap().parse().unwrap();
            let mut body = vec![0; size]; reader.read_exact(&mut body).unwrap();
            let body = String::from_utf8(body).unwrap();
            assert!(body.contains(&name) && body.contains("example-test-audio"));
            assert!(!body.contains("/tmp/"));
            let json = r#"{"id":"0123456789abcdef0123456789abcdef","kind":"audio","name":"music.wav"}"#;
            write!(reader.get_mut(), "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", json.len(), json).unwrap();
        });
        assert_eq!(import_selection(Some(path.clone()), port, "test-token").unwrap().unwrap()["kind"], "audio");
        server.join().unwrap(); std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn image_cancel_and_limits_never_contact_the_backend() {
        assert_eq!(import_images(None, 5, 0, "test-token").unwrap(), None);
        assert_eq!(import_images(Some(vec![]), 5, 0, "test-token").unwrap(), None);
        assert!(import_images(Some(vec![PathBuf::from("one"); 6]), 5, 0, "test-token").is_err());
        assert!(import_images(None, 6, 0, "test-token").is_err());
    }

    #[test]
    fn partial_image_imports_preserve_success_and_report_errors() {
        let response = import_images(Some(vec![PathBuf::from("/missing-news-photo.png")]), 1, 0, "test-token").unwrap().unwrap();
        assert_eq!(response["assets"].as_array().unwrap().len(), 0);
        assert_eq!(response["errors"].as_array().unwrap().len(), 1);
        assert!(!response["errors"][0].as_str().unwrap().contains("/missing"));
    }

    #[test]
    fn successful_images_survive_a_later_import_error() {
        let root = std::env::temp_dir().join(format!("reel-photos-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&root).unwrap();
        let one = root.join("one.png"); let two = root.join("two.png");
        std::fs::write(&one, b"first photo data").unwrap(); std::fs::write(&two, b"second photo data").unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            for index in 0..2 {
                let (stream, _) = listener.accept().unwrap();
                stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut reader = BufReader::new(stream); let mut headers = String::new();
                loop { let mut line = String::new(); reader.read_line(&mut line).unwrap(); headers.push_str(&line); if line == "\r\n" { break; } }
                assert!(headers.starts_with("POST /api/reel-creator/assets/image HTTP/1.1"));
                assert!(headers.to_lowercase().contains("x-manager-token: test-token"));
                let size: usize = headers.lines().find_map(|line| line.to_lowercase().strip_prefix("content-length: ").map(str::to_string)).unwrap().parse().unwrap();
                let mut body = vec![0; size]; reader.read_exact(&mut body).unwrap();
                let (status, json) = if index == 0 { ("200 OK", r#"{"id":"0123456789abcdef0123456789abcdef","kind":"image","name":"one.png"}"#) }
                    else { ("400 Bad Request", r#"{"detail":"Unreadable image"}"#) };
                write!(reader.get_mut(), "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{json}", json.len()).unwrap();
            }
        });
        let result = import_images(Some(vec![one, two]), 2, port, "test-token").unwrap().unwrap();
        assert_eq!(result["assets"].as_array().unwrap().len(), 1);
        assert_eq!(result["errors"].as_array().unwrap().len(), 1);
        assert!(result["errors"][0].as_str().unwrap().contains("two.png: Unreadable image"));
        server.join().unwrap(); std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn empty_files_are_rejected_before_upload() {
        let path = std::env::temp_dir().join(format!("reel-empty-{}.wav", uuid::Uuid::new_v4()));
        std::fs::write(&path, b"").unwrap();
        assert!(import_selection(Some(path.clone()), 0, "test-token").unwrap_err().contains("25 MB"));
        std::fs::remove_file(path).unwrap();
    }
}
