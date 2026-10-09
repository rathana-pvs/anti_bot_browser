fn main() {
    // Tauri copies resources in place. On Unix this fails with ETXTBSY when a
    // previous desktop/backend still runs the bundled executable. Replace the
    // destination inode first; existing processes keep their original inode.
    #[cfg(unix)]
    refresh_binary_resources().expect("could not stage backend resources");
    tauri_build::build()
}

#[cfg(unix)]
fn refresh_binary_resources() -> std::io::Result<()> {
    use std::{env, ffi::OsStr, fs, path::PathBuf};

    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo OUT_DIR is missing"));
    let target_dir = out_dir
        .ancestors()
        .find(|path| path.file_name() == Some(OsStr::new("build")))
        .and_then(|path| path.parent())
        .expect("invalid Cargo OUT_DIR");
    let source_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("binaries");
    if !source_dir.exists() {
        return Ok(());
    }
    let destination_dir = target_dir.join("binaries");
    fs::create_dir_all(&destination_dir)?;
    for entry in fs::read_dir(source_dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_file() {
            continue;
        }
        let destination = destination_dir.join(entry.file_name());
        if destination.exists() {
            let staged = destination_dir.join(format!(
                ".stage-{}-{}",
                std::process::id(),
                entry.file_name().to_string_lossy()
            ));
            fs::copy(entry.path(), &staged)?;
            fs::rename(staged, destination)?;
        }
    }
    Ok(())
}
