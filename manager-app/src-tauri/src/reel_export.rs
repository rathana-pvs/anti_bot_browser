//! Stream a verified local render directly to native Downloads (including WSL).
use std::fs::{self, OpenOptions};
use std::io::{BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;
use tauri::{AppHandle, Manager, State};

use crate::{available_download_path, ApiSession};

fn download_to(port: u16, token: &str, job_id: &str, downloads: &Path) -> Result<PathBuf, String> {
    if job_id.len() != 32 || !job_id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid render ID.".into());
    }
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(3))
        .map_err(|_| "Could not connect to the local renderer.".to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(30))).map_err(|_| "Could not prepare video export.")?;
    stream.set_write_timeout(Some(Duration::from_secs(3))).map_err(|_| "Could not prepare video export.")?;
    write!(stream, "GET /api/reel-creator/jobs/{job_id}/download HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Manager-Token: {token}\r\nConnection: close\r\n\r\n")
        .map_err(|_| "Could not request the video.".to_string())?;
    let mut reader = BufReader::new(stream);
    // Bound the header before parsing; never buffer the video through IPC.
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
        return Err("The video is not available. Refresh its render status and try again.".into());
    }
    let mut size = None;
    let mut video_type = false;
    for line in headers.lines().skip(1) {
        if let Some((key, value)) = line.split_once(':') {
            match key.to_ascii_lowercase().as_str() {
                "content-length" => size = value.trim().parse::<u64>().ok(),
                "content-type" => video_type = value.trim() == "video/mp4",
                "transfer-encoding" => return Err("Unsupported export response.".into()),
                _ => {}
            }
        }
    }
    let size = size.filter(|n| *n > 0 && *n <= 128 * 1024 * 1024)
        .ok_or("The video size is invalid or exceeds 128 MB.")?;
    if !video_type { return Err("The renderer did not return an MP4 video.".into()); }
    fs::create_dir_all(downloads).map_err(|_| "Could not create the Downloads folder.")?;
    let path = available_download_path(downloads, &format!("reel_{job_id}.mp4"));
    let mut file = OpenOptions::new().create_new(true).write(true).open(&path)
        .map_err(|_| "Could not create the video in Downloads. Try again or check folder permissions.")?;
    let result = (|| {
        let copied = std::io::copy(&mut reader.take(size), &mut file)
            .map_err(|_| "The video transfer was interrupted.")?;
        if copied != size { return Err("The video transfer was incomplete."); }
        file.flush().map_err(|_| "Could not finish saving the video.")?;
        file.sync_all().map_err(|_| "Could not finish saving the video.")?;
        Ok(())
    })();
    drop(file);
    if let Err(error) = result {
        let _ = fs::remove_file(&path);
        return Err(error.into());
    }
    Ok(path)
}

#[tauri::command]
pub async fn export_reel_video(app: AppHandle, session: State<'_, ApiSession>, job_id: String) -> Result<String, String> {
    let downloads = app.path().download_dir().map_err(|_| "Could not locate the Downloads folder.")?;
    let port = session.port;
    let token = session.token.clone();
    tauri::async_runtime::spawn_blocking(move || {
        download_to(port, &token, &job_id, &downloads)
            .map(|path| format!("Saved to {}", path.display()))
    }).await.map_err(|_| "Video export stopped unexpectedly.".to_string())?
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
        let path = download_to(port, "test-token", id, &root).unwrap();
        thread.join().unwrap();
        assert_eq!(fs::read(original).unwrap(), b"existing");
        assert_eq!(fs::read(path).unwrap(), b"video");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn removes_incomplete_video_and_rejects_invalid_ids() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let (port, thread) = serve(b"HTTP/1.0 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: 9\r\n\r\nshort");
        assert!(download_to(port, "test-token", "0123456789abcdef0123456789abcdef", &root).is_err());
        thread.join().unwrap();
        assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
        assert!(download_to(1, "test-token", "../bad", &root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
