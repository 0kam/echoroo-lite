#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::Manager;

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

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
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
        .invoke_handler(tauri::generate_handler![connection])
        .build(tauri::generate_context!())
        .expect("Could not initialize the desktop application");
    app.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(mut child) = app.state::<Sidecar>().child.lock().unwrap().take() {
                if let Some(mut stdin) = child.stdin.take() {
                    let _ = stdin.write_all(b"\n");
                }
                // Wait for the active model call and its worker cleanup before exiting.
                let _ = child.wait();
            }
        }
    });
}
