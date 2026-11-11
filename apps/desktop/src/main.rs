#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};
use tauri::{ipc::Channel, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Clone, serde::Serialize, serde::Deserialize)]
struct Connection {
    url: String,
    token: String,
}
struct Sidecar {
    child: Mutex<Option<Child>>,
    connection: Result<Connection, String>,
}

#[tauri::command]
fn connection(state: tauri::State<Sidecar>) -> Result<Connection, String> {
    state.connection.clone()
}

fn launch(
    data: PathBuf,
    resources: PathBuf,
) -> Result<(Child, Connection), Box<dyn std::error::Error>> {
    let mut command;
    let models;
    if cfg!(debug_assertions) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let python = if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        };
        command = Command::new(root.join(python));
        command.arg(root.join("apps/sidecar/server.py"));
        models = root.join("poc/models");
    } else {
        let binary = if cfg!(windows) {
            "echoroo-sidecar.exe"
        } else {
            "echoroo-sidecar"
        };
        command = Command::new(std::env::current_exe()?.parent().unwrap().join(binary));
        models = resources.join("models");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command
        .args(["--data"])
        .arg(data)
        .arg("--models")
        .arg(
            std::env::var_os("ECHOROO_MODELS")
                .map(PathBuf::from)
                .unwrap_or(models),
        )
        .arg("--parent-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    stdout.read_line(&mut line)?;
    let connection = match serde_json::from_str(&line) {
        Ok(connection) => connection,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error.into());
        }
    };
    // Drain library diagnostics so a full stdout pipe cannot stop inference.
    std::thread::spawn(move || {
        let _ = std::io::copy(&mut stdout, &mut std::io::sink());
    });
    Ok((child, connection))
}

/// Tells the sidecar to stop and waits until it has gone.
fn stop_sidecar(app: &tauri::AppHandle) {
    if let Some(mut child) = app.state::<Sidecar>().child.lock().unwrap().take() {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(b"\n");
        }
        // Wait for the active model call and its worker cleanup before exiting.
        let _ = child.wait();
    }
}

/// The update found by the last check and, once downloaded, its verified package.
#[derive(Default)]
struct PendingUpdate {
    update: Mutex<Option<Update>>,
    package: Mutex<Option<Vec<u8>>>,
}
#[derive(serde::Serialize)]
struct AvailableUpdate {
    version: String,
    /// Download size in bytes, when the release's latest.json states it.
    size: Option<u64>,
}

fn update_failed(error: tauri_plugin_updater::Error) -> String {
    eprintln!("App update: {error}");
    "update_failed".into()
}

#[tauri::command]
async fn update_check(
    app: tauri::AppHandle,
    pending: tauri::State<'_, PendingUpdate>,
) -> Result<Option<AvailableUpdate>, String> {
    let exiting = app.clone();
    let update = app
        .updater_builder()
        .timeout(Duration::from_secs(30))
        // Windows only: the installer overwrites echoroo-sidecar.exe, and the updater ends the
        // process itself, so `RunEvent::Exit` never comes. This replaces the plugin's own hook.
        .on_before_exit(move || {
            stop_sidecar(&exiting);
            exiting.cleanup_before_exit();
        })
        .build()
        .map_err(update_failed)?
        .check()
        .await
        .map_err(update_failed)?;
    let available = update.as_ref().map(|update| AvailableUpdate {
        version: update.version.clone(),
        // `size` is our own addition to the entry of each platform in latest.json.
        size: update.raw_json["platforms"]
            .as_object()
            .and_then(|platforms| {
                platforms.values().find(|entry| {
                    entry["url"]
                        .as_str()
                        .and_then(|url| url.parse().ok())
                        .as_ref()
                        == Some(&update.download_url)
                })
            })
            .and_then(|entry| entry["size"].as_u64()),
    });
    *pending.update.lock().unwrap() = update;
    Ok(available)
}

/// Downloads the update into memory and checks its signature; `progress` gets (bytes so far, total).
#[tauri::command]
async fn update_download(
    pending: tauri::State<'_, PendingUpdate>,
    progress: Channel<(u64, Option<u64>)>,
) -> Result<(), String> {
    let update = pending
        .update
        .lock()
        .unwrap()
        .clone()
        .ok_or("update_failed")?;
    let mut done = 0;
    let package = update
        .download(
            |chunk, total| {
                let before = done;
                done += chunk as u64;
                // One message per megabyte is enough for the progress line.
                if done / 1_000_000 != before / 1_000_000 {
                    let _ = progress.send((done, total));
                }
            },
            || {},
        )
        .await
        .map_err(update_failed)?;
    *pending.package.lock().unwrap() = Some(package);
    Ok(())
}

/// Replaces the app with the downloaded update and starts the new version.
#[tauri::command]
async fn update_install(
    app: tauri::AppHandle,
    pending: tauri::State<'_, PendingUpdate>,
) -> Result<(), String> {
    if cfg!(debug_assertions) {
        // A development build is not an installed app: installing would overwrite the build folder.
        return Err("update_failed".into());
    }
    let update = pending
        .update
        .lock()
        .unwrap()
        .clone()
        .ok_or("update_failed")?;
    // Windows: this starts the installer and never returns; the installer opens the new version.
    update
        .install(
            pending
                .package
                .lock()
                .unwrap()
                .as_deref()
                .ok_or("update_failed")?,
        )
        .map_err(update_failed)?;
    // macOS and Linux: the files are replaced; restarting goes through `RunEvent::Exit`.
    app.request_restart();
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(PendingUpdate::default())
        .setup(|app| {
            let data = std::env::var_os("ECHOROO_DATA")
                .map(PathBuf::from)
                .unwrap_or(app.path().app_data_dir()?);
            let resources = app.path().resource_dir()?;
            let sidecar = match launch(data, resources) {
                Ok((child, connection)) => Sidecar {
                    child: Mutex::new(Some(child)),
                    connection: Ok(connection),
                },
                Err(error) => {
                    eprintln!("Sidecar startup: {error}");
                    Sidecar {
                        child: Mutex::new(None),
                        connection: Err("disconnected".into()),
                    }
                }
            };
            app.manage(sidecar);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            connection,
            update_check,
            update_download,
            update_install
        ])
        .build(tauri::generate_context!())
        .expect("Could not initialize the desktop application");
    app.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            stop_sidecar(app);
        }
    });
}
