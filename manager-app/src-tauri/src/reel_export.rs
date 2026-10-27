//! Stream a verified local render (a video, or a photo made from one scene) directly to native Downloads (including WSL).
use std::fs::{self, OpenOptions};
use std::io::{BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;
use tauri::{AppHandle, Manager, State};

use crate::{available_download_path, ApiSession};

/// What is exported: where the renderer serves it, what it must be, and the name it is saved under.
struct Export {
    noun: &'static str,
    route: &'static str,
    content_type: &'static str,
    prefix: &'static str,
    extension: &'static str,
}

const VIDEO: Export = Export { noun: "video", route: "jobs", content_type: "video/mp4", prefix: "reel", extension: "mp4" };
const PHOTO: Export = Export { noun: "photo", route: "photos", content_type: "image/jpeg", prefix: "reel_photo", extension: "jpg" };

fn download_to(port: u16, token: &str, id: &str, downloads: &Path, export: &Export) -> Result<PathBuf, String> {
    let noun = export.noun;
    if id.len() != 32 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid render ID.".into());
    }
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(3))
        .map_err(|_| "Could not connect to the local renderer.".to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(30))).map_err(|_| format!("Could not prepare {noun} export."))?;
    stream.set_write_timeout(Some(Duration::from_secs(3))).map_err(|_| format!("Could not prepare {noun} export."))?;
    write!(stream, "GET /api/reel-creator/{}/{id}/download HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Manager-Token: {token}\r\nConnection: close\r\n\r\n", export.route)
        .map_err(|_| format!("Could not request the {noun}."))?;
    let mut reader = BufReader::new(stream);
    // Bound the header before parsing; never buffer the file through IPC.
    let mut headers = Vec::new();
    loop {
        let byte = reader.by_ref().take(1).bytes().next()
            .ok_or("Incomplete export response.")?.map_err(|_| "Could not read the export response.")?;
        headers.push(byte);
        if headers.ends_with(b"\r\n\r\n") { break; }
        if headers.len() > 16_384 { return Err("Invalid export response.".into()); }
    }
    let headers = String::from_utf8(headers).map_err(|_| "Invalid export response.")?;
    if !headers.starts_with("HTTP/1.0 200 ") && !headers.starts_with("HTTP/1.1 200 ") {
        return Err(format!("The {noun} is not available. Refresh its render status and try again."));
    }
    let mut size = None;
    let mut expected_type = false;
    for line in headers.lines().skip(1) {
        if let Some((key, value)) = line.split_once(':') {
            match key.to_ascii_lowercase().as_str() {
                "content-length" => size = value.trim().parse::<u64>().ok(),
                "content-type" => expected_type = value.trim() == export.content_type,
                "transfer-encoding" => return Err("Unsupported export response.".into()),
                _ => {}
            }
        }
    }
    let size = size.filter(|n| *n > 0 && *n <= 128 * 1024 * 1024)
        .ok_or(format!("The {noun} size is invalid or exceeds 128 MB."))?;
    if !expected_type { return Err(format!("The renderer did not return a {noun} ({}).", export.content_type)); }
    fs::create_dir_all(downloads).map_err(|_| "Could not create the Downloads folder.")?;
    let path = available_download_path(downloads, &format!("{}_{id}.{}", export.prefix, export.extension));
    let mut file = OpenOptions::new().create_new(true).write(true).open(&path)
        .map_err(|_| format!("Could not create the {noun} in Downloads. Try again or check folder permissions."))?;
    let result = (|| {
        let copied = std::io::copy(&mut reader.take(size), &mut file)
            .map_err(|_| format!("The {noun} transfer was interrupted."))?;
        if copied != size { return Err(format!("The {noun} transfer was incomplete.")); }
        file.flush().map_err(|_| format!("Could not finish saving the {noun}."))?;
        file.sync_all().map_err(|_| format!("Could not finish saving the {noun}."))?;
        Ok(())
    })();
    drop(file);
    if let Err(error) = result {
        let _ = fs::remove_file(&path);
        return Err(error);
    }
    Ok(path)
}

async fn export(app: AppHandle, session: State<'_, ApiSession>, id: String, export: &'static Export) -> Result<String, String> {
    let downloads = app.path().download_dir().map_err(|_| "Could not locate the Downloads folder.")?;
    let port = session.port;
    let token = session.token.clone();
    tauri::async_runtime::spawn_blocking(move || {
        download_to(port, &token, &id, &downloads, export)
            .map(|path| format!("Saved to {}", path.display()))
    }).await.map_err(|_| format!("The {} export stopped unexpectedly.", export.noun))?
}

#[tauri::command]
pub async fn export_reel_video(app: AppHandle, session: State<'_, ApiSession>, job_id: String) -> Result<String, String> {
    export(app, session, job_id, &VIDEO).await
}

#[tauri::command]
pub async fn export_reel_photo(app: AppHandle, session: State<'_, ApiSession>, photo_id: String) -> Result<String, String> {
    export(app, session, photo_id, &PHOTO).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    fn serve(response: &'static [u8]) -> (u16, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let thread = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                stream.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
                assert!(request.len() < 2048);
            }
            assert!(String::from_utf8_lossy(&request).contains("X-Manager-Token: test-token"));
            stream.write_all(response).unwrap();
        });
        (port, thread)
    }

    #[test]
    fn streams_video_without_overwriting_an_existing_export() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        fs::create_dir_all(&root).unwrap();
        let id = "0123456789abcdef0123456789abcdef";
        let original = root.join(format!("reel_{id}.mp4"));
        fs::write(&original, b"existing").unwrap();
        let (port, thread) = serve(b"HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: 5\r\n\r\nvideo");
        let path = download_to(port, "test-token", id, &root, &VIDEO).unwrap();
        thread.join().unwrap();
        assert_eq!(fs::read(original).unwrap(), b"existing");
        assert_eq!(fs::read(path).unwrap(), b"video");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn removes_incomplete_video_and_rejects_invalid_ids() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let (port, thread) = serve(b"HTTP/1.0 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: 9\r\n\r\nshort");
        assert!(download_to(port, "test-token", "0123456789abcdef0123456789abcdef", &root, &VIDEO).is_err());
        thread.join().unwrap();
        assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
        assert!(download_to(1, "test-token", "../bad", &root, &VIDEO).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn saves_a_photo_and_refuses_a_video_in_its_place() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let id = "0123456789abcdef0123456789abcdef";
        let (port, thread) = serve(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\njpeg");
        let path = download_to(port, "test-token", id, &root, &PHOTO).unwrap();
        thread.join().unwrap();
        assert_eq!(path.file_name().unwrap(), format!("reel_photo_{id}.jpg").as_str());
        assert_eq!(fs::read(&path).unwrap(), b"jpeg");
        let (port, thread) = serve(b"HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: 5\r\n\r\nvideo");
        assert!(download_to(port, "test-token", id, &root, &PHOTO).is_err());
        thread.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
