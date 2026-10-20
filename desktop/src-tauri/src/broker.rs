//! Headless broker (Task 9 plan D1, D2, D19). The app's own executable started with exactly `--controller` reads one
//! non-secret `factory-broker/1` line, reads the vault, `settings.json` and `executor-profiles.json`, starts the bundled
//! sidecar with exactly `--controller` and no added environment, writes one `factory-controller/1` line with the
//! scoped credentials, and keeps that pipe open until the sidecar exits. EOF on the pipe means stop. It never restarts
//! the sidecar and never opens a window or a WebView.

use crate::profiles::{self, Vault, PROFILE_SECRETS};
use serde_json::{json, Map, Value};
use std::{fs, io::{self, BufRead, Read, Write}, path::{Component, Path, PathBuf}, process::{Command, Stdio},
    time::{SystemTime, UNIX_EPOCH}};

pub const CONTROLLER_ARG: &str = "--controller";
pub const BROKER_SCHEMA: &str = "factory-broker/1";
pub const START_SCHEMA: &str = "factory-controller/1";
pub const CONTROLLER_TRACKING_FIELDS: [&str; 8] = ["MLFLOW_TRACKING_URI", "FACTORY_MLFLOW_ARTIFACT_LOCATION",
    "FACTORY_OPERATOR", "auth_mode", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
    "MLFLOW_TRACKING_TOKEN"];
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
pub const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
pub const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
pub const SIDECAR_CREATION_FLAGS: u32 = CREATE_NO_WINDOW;
pub const MAX_BROKER_LINE: usize = 64 * 1024;
pub const MAX_APP_ROOT: usize = 4000;
pub const MAX_START_BYTES: usize = 1024 * 1024;     // = controller_entry.MAX_START_BYTES (plan §4 9.4)
pub const EXIT_BAD_START: i32 = 10;
pub const EXIT_SETTINGS: i32 = 11;
pub const EXIT_SPAWN: i32 = 12;

const ACCESS_DENIED: i32 = 5;        // ERROR_ACCESS_DENIED: the parent's job object does not allow breakaway
const MLFLOW_SECRETS: [&str; 2] = ["MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN"];
const INVALID_START: &str = "invalid broker start line";
const ROOT_UNUSABLE: &str = "앱 저장 폴더 경로를 백그라운드 실행기에 전달할 수 없습니다.";
const STATUS_UNKNOWN: &str = "컨트롤러 상태를 확인할 수 없습니다.";
const SETTINGS_FAILED: &str = "백그라운드 실행기가 설정·실행 프로필이나 Windows 자격 증명을 읽지 못해 컨트롤러를 시작하지 않았습니다.";
const START_TOO_LARGE: &str = "실행 프로필과 MLflow 설정이 너무 커서 컨트롤러를 시작하지 않았습니다.";
const SPAWN_FAILED: &str = "백그라운드 실행기가 학습 엔진을 실행하지 못했습니다. 설치 파일을 확인해 주세요.";
const START_REFUSED: &str = "학습 엔진이 컨트롤러 시작 정보를 받아들이지 않아 컨트롤러가 시작되지 않았습니다. 앱을 다시 설치해 주세요.";
const SETTINGS_REFUSED: &str = "MLflow 설정이 보안 규칙(인증값이 있으면 HTTPS)에 맞지 않아 컨트롤러가 시작되지 않았습니다. 설정을 확인한 뒤 다시 시작하세요.";
const CONTROLLER_FAILED: &str = "컨트롤러가 예기치 않게 종료되었습니다. 진행 중이던 단계는 컨트롤러를 다시 시작하면 다시 확인합니다.";

/// The started sidecar as the broker sees it: its stdin pipe and a blocking wait for its exit code.
pub struct Sidecar {
    pub stdin: Box<dyn Write>,
    pub wait: Box<dyn FnOnce() -> i32>,
}

/// Headless broker mode: argv is exactly `[exe, "--controller"]`.
pub fn is_controller_mode(args: &[String]) -> bool { args.len() == 2 && args[1] == CONTROLLER_ARG }

/// The bundled sidecar, by the same rule as `factory_action` (F1).
pub fn sidecar_binary() -> Result<PathBuf, String> {
    Ok(if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/factory-bridge-x86_64-pc-windows-msvc.exe")
    } else {
        std::env::current_exe().map_err(|_| "앱 경로 오류")?.parent().ok_or("앱 경로 오류")?.join("factory-bridge.exe")
    })
}

fn set_creation_flags(command: &mut Command, flags: u32) {
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(flags);
    }
    #[cfg(not(windows))]
    let _ = (command, flags);
}

/// `factory-bridge --controller`: stdin piped (the start line, then the lifeline), stdout and stderr discarded, no
/// window, and no environment added (the sidecar pops inherited credential variables itself).
pub fn sidecar_command(binary: &Path) -> Command {
    let mut command = Command::new(binary);
    command.arg(CONTROLLER_ARG).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null());
    set_creation_flags(&mut command, SIDECAR_CREATION_FLAGS);
    command
}

pub fn broker_creation_flags(breakaway: bool) -> u32 {
    CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | if breakaway { CREATE_BREAKAWAY_FROM_JOB } else { 0 }
}

/// This executable with exactly `--controller`, detached from the window: its own process group, no window, out of
/// the GUI's job object when the job allows it.
pub fn broker_command(exe: &Path, breakaway: bool) -> Command {
    let mut command = Command::new(exe);
    command.arg(CONTROLLER_ARG).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null());
    set_creation_flags(&mut command, broker_creation_flags(breakaway));
    command
}

/// Tries `CREATE_BREAKAWAY_FROM_JOB` first and retries once without it only when the job refuses (access denied).
pub fn spawn_breakaway_or_fallback<T>(mut spawn: impl FnMut(bool) -> io::Result<T>) -> io::Result<T> {
    match spawn(true) {
        Err(error) if error.raw_os_error() == Some(ACCESS_DENIED) => spawn(false),
        other => other,
    }
}

/// The one non-secret line the window hands the broker: `{"schema": "factory-broker/1", "app_root": <abs path>}`.
pub fn broker_start_line(app_root: &Path) -> Result<String, String> {
    let root = app_root.to_str().ok_or(ROOT_UNUSABLE)?;
    let line = json!({"schema": BROKER_SCHEMA, "app_root": root}).to_string();
    parse_broker_start(&line).map_err(|_| ROOT_UNUSABLE)?;      // never send what the broker refuses
    Ok(line + "\n")
}

/// Exactly `schema` and `app_root`; the root is an absolute local path (no UNC or device prefix, no `..`).
pub fn parse_broker_start(line: &str) -> Result<PathBuf, String> {
    let line = line.strip_suffix('\n').unwrap_or(line);
    let line = line.strip_suffix('\r').unwrap_or(line);
    if line.is_empty() || line.len() > MAX_BROKER_LINE { return Err(INVALID_START.into()); }
    let Ok(Value::Object(start)) = serde_json::from_str::<Value>(line) else { return Err(INVALID_START.into()) };
    if start.len() != 2 || start.get("schema").and_then(Value::as_str) != Some(BROKER_SCHEMA) {
        return Err(INVALID_START.into());
    }
    let root = start.get("app_root").and_then(Value::as_str).ok_or(INVALID_START)?;
    let mut prefix = root.chars();
    let network = matches!((prefix.next(), prefix.next()), (Some('\\' | '/'), Some('\\' | '/')));
    let path = PathBuf::from(root);
    if root.is_empty() || root.len() > MAX_APP_ROOT || network || root.chars().any(char::is_control)
        || !path.is_absolute() || path.components().any(|part| part == Component::ParentDir) {
        return Err(INVALID_START.into());
    }
    Ok(path)
}

/// `graph_store.OWNER_ID`-compatible owner of this broker's controller.
pub fn owner_id(pid: u32, nanos: u128) -> String { format!("desktop.{pid}.{nanos}") }

/// The `factory-controller/1` line (plan §4 9.4 `ControllerStart`): only `CONTROLLER_TRACKING_FIELDS`, filtered by
/// `auth_mode` exactly as `filter_mlflow_auth` filters the sidecar's settings, and each profile's public fields with
/// its own RunPod key. Never S3, HF or the legacy RunPod key, and never `StartSettings`.
pub fn controller_message(state_root: &Path, owner_id: &str, settings_with_secrets: &Map<String, Value>,
                          profiles_with_secrets: &Value) -> Value {
    let mut settings = settings_with_secrets.clone();
    crate::filter_mlflow_auth(&mut settings, true);
    let tracking: Map<String, Value> = CONTROLLER_TRACKING_FIELDS.iter()
        .filter_map(|name| Some((name.to_string(), Value::String(settings.get(*name)?.as_str()?.to_owned()))))
        .collect();
    let profiles: Vec<Value> = profiles_with_secrets.as_array().into_iter().flatten().filter_map(|profile| {
        let secrets: Map<String, Value> = PROFILE_SECRETS.iter()
            .filter_map(|name| Some((name.to_string(), Value::String(profile.get("secrets")?.get(*name)?.as_str()?.to_owned()))))
            .collect();
        Some(json!({"profile_id": profile.get("profile_id")?.as_str()?, "fields": profile.get("fields")?.as_object()?,
                    "secrets": secrets}))
    }).collect();
    json!({"schema": START_SCHEMA, "state_root": state_root.to_string_lossy(), "owner_id": owner_id,
           "tracking": tracking, "profiles": profiles})
}

/// `controller_start`'s decision from the `controller_status` workflow result: start for `absent` or `stale`, not for
/// `running`; anything else (a missing 9.4 action included) is an error, so no broker starts blind.
pub fn should_start(status: &Value) -> Result<bool, String> {
    if status.get("status").and_then(Value::as_str) != Some("ok") {
        let messages: Vec<&str> = status.get("issues").and_then(Value::as_array).into_iter().flatten()
            .filter_map(|issue| issue.get("message").and_then(Value::as_str)).collect();
        return Err(if messages.is_empty() { STATUS_UNKNOWN.into() } else { format!("{STATUS_UNKNOWN} ({})", messages.join(" ")) });
    }
    match status.pointer("/data/controller/state").and_then(Value::as_str) {
        Some("absent" | "stale") => Ok(true),
        Some("running") => Ok(false),
        _ => Err(STATUS_UNKNOWN.into()),
    }
}

fn read_start_line(input: &mut dyn BufRead) -> Option<String> {
    let mut line = Vec::new();
    input.take(MAX_BROKER_LINE as u64 + 2).read_until(b'\n', &mut line).ok()?;
    String::from_utf8(line).ok()
}

/// The tracking settings plus only the MLflow secret the saved `auth_mode` uses (decided by `filter_mlflow_auth`
/// itself); S3, HF and the legacy RunPod key are never read from the vault.
fn tracking_settings(app_root: &Path, vault: &dyn Vault) -> Result<Map<String, Value>, String> {
    let public = crate::read_settings_file(app_root)?;
    let mut settings: Map<String, Value> = CONTROLLER_TRACKING_FIELDS.iter()
        .filter_map(|name| Some((name.to_string(), Value::String(public.get(*name)?.as_str()?.to_owned()))))
        .collect();
    let mut probe = settings.clone();
    for name in MLFLOW_SECRETS { probe.insert(name.into(), Value::Null); }
    crate::filter_mlflow_auth(&mut probe, true);
    for name in MLFLOW_SECRETS.into_iter().filter(|name| probe.contains_key(*name)) {
        if let Some(secret) = vault.get(name)? { settings.insert(name.into(), Value::String(secret)); }
    }
    crate::filter_mlflow_auth(&mut settings, true);
    Ok(settings)
}

fn start_message(app_root: &Path, vault: &dyn Vault, owner_id: &str) -> Result<Value, String> {
    let settings = tracking_settings(app_root, vault)?;
    let profiles = profiles::read_profiles(app_root, vault, true)?;
    Ok(controller_message(&app_root.join("state"), owner_id, &settings, &profiles["profiles"]))
}

fn status_path(app_root: &Path) -> PathBuf { app_root.join("state").join("controller").join("broker.json") }

/// D19: `state/controller/broker.json` `{at, code, error}` with a fixed code and text, for failures before the sidecar
/// starts and for a sidecar that exits with an error (`exit_reason`). Best effort: the broker has no other channel
/// to the window. Written to a temporary file and renamed, so a reader never sees a partial status.
fn record_failure(app_root: &Path, code: &str, error: &str) {
    let at = SystemTime::now().duration_since(UNIX_EPOCH).map(|elapsed| elapsed.as_secs_f64()).unwrap_or(0.0);
    let path = status_path(app_root);
    let Some(directory) = path.parent() else { return };
    if fs::create_dir_all(directory).is_err() { return; }
    let temporary = directory.join("broker.tmp");
    if fs::write(&temporary, json!({"at": at, "code": code, "error": error}).to_string()).is_err()
        || fs::rename(&temporary, &path).is_err() {
        let _ = fs::remove_file(temporary);
    }
}

/// The broker's whole life after argv: 10 for a bad start line, 11 when settings, profiles or the vault cannot be
/// read (or the start message would exceed 1 MiB), 12 when the sidecar cannot start, else the sidecar's exit code
/// (with a public reason in `broker.json` unless it is 0 or 3).
pub fn serve_broker(input: &mut dyn BufRead, vault: &dyn Vault, owner_id: &str,
                    spawn: &mut dyn FnMut() -> Result<Sidecar, String>) -> i32 {
    let Some(app_root) = read_start_line(input).and_then(|line| parse_broker_start(&line).ok()) else {
        return EXIT_BAD_START;
    };
    let line = match start_message(&app_root, vault, owner_id) {
        Ok(message) => message.to_string() + "\n",
        Err(_) => {
            record_failure(&app_root, "settings_unavailable", SETTINGS_FAILED);
            return EXIT_SETTINGS;
        }
    };
    if line.len() > MAX_START_BYTES {
        record_failure(&app_root, "start_too_large", START_TOO_LARGE);
        return EXIT_SETTINGS;
    }
    let Ok(Sidecar { mut stdin, wait }) = spawn() else {
        record_failure(&app_root, "sidecar_unavailable", SPAWN_FAILED);
        return EXIT_SPAWN;
    };
    let _ = fs::remove_file(status_path(&app_root));     // a stale failure no longer describes this start
    // One line, then the pipe stays open: its EOF (this broker ending) tells the controller to drain and stop.
    let _ = stdin.write_all(line.as_bytes()).and_then(|()| stdin.flush());
    drop(line);
    let code = wait();
    drop(stdin);
    if let Some((status, error)) = exit_reason(code) { record_failure(&app_root, status, error); }
    code
}

/// D19 after the start: the sidecar's output is never read, so an exit other than a drained stop (0) or a duplicate
/// controller (3, `owner_held`, the other controller keeps running) leaves a fixed public code and text.
fn exit_reason(code: i32) -> Option<(&'static str, &'static str)> {
    match code {
        0 | 3 => None,
        2 => Some(("controller_start_refused", START_REFUSED)),
        4 => Some(("controller_settings_refused", SETTINGS_REFUSED)),
        _ => Some(("controller_failed", CONTROLLER_FAILED)),
    }
}

fn spawn_sidecar() -> Result<Sidecar, String> {
    let mut child = sidecar_command(&sidecar_binary()?).spawn().map_err(|_| SPAWN_FAILED.to_owned())?;
    let stdin = child.stdin.take().ok_or(SPAWN_FAILED)?;
    // `Child::wait` closes a stdin it still holds; this one was taken out, so the pipe stays open while waiting.
    Ok(Sidecar { stdin: Box::new(stdin), wait: Box::new(move || child.wait().ok().and_then(|status| status.code()).unwrap_or(1)) })
}

/// `main()` calls this for `[exe, "--controller"]` before any Tauri code runs, then exits with its code.
pub fn run_broker(vault: &dyn Vault) -> i32 {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|elapsed| elapsed.as_nanos()).unwrap_or(0);
    let owner = owner_id(std::process::id(), nanos);
    let mut input = io::stdin().lock();
    serve_broker(&mut input, vault, &owner, &mut spawn_sidecar)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profiles::testing::*;
    use crate::profiles::{profile_secret_entry, save_profile};
    use serde_json::json;
    use std::{collections::BTreeSet, ffi::OsStr, fs, io, sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex}};

    /// The `factory-controller/1` line `controller_message` builds from `sample_settings()` and `sample_profiles()`.
    /// tests/test_native_contract.py validates this same text against the Python side (9.4's `ControllerStart`).
    const CONTROLLER_MESSAGE_SAMPLE: &str = r#"{"schema":"factory-controller/1","state_root":"C:\\factory\\state","owner_id":"desktop.4242.1760918400000000000","tracking":{"MLFLOW_TRACKING_URI":"https://mlflow.invalid","FACTORY_MLFLOW_ARTIFACT_LOCATION":"","FACTORY_OPERATOR":"researcher","auth_mode":"token","MLFLOW_TRACKING_TOKEN":"synthetic-token-kkkkkkkkkkkk"},"profiles":[{"profile_id":"gpu-a","fields":{"provider":"existing_runpod","name":"Pod A","pod_id":"podA1","ssh_fingerprint":"SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","ssh_key_file":"C:\\keys\\id_ed25519"},"secrets":{"RUNPOD_API_KEY":"synthetic-profile-a-kkkkkkkkkkkk"}},{"profile_id":"lab","fields":{"provider":"ssh_server","name":"Lab server","server_ssh_host":"gpu.example","server_ssh_port":"22","server_ssh_username":"researcher","server_ssh_fingerprint":"SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB","server_remote_root":"/home/researcher/factory","server_ssh_key_file":"C:\\keys\\gpu_ed25519","server_total_hourly_cost_usd":"0","server_selected_gpu_uuids":"[\"GPU-11111111-2222-3333-4444-555555555555\"]"},"secrets":{}}]}"#;

    fn obj(value: Value) -> Map<String, Value> { value.as_object().unwrap().clone() }
    fn args(items: &[&str]) -> Vec<String> { items.iter().map(|item| item.to_string()).collect() }
    fn runpod_fields() -> Value {
        json!({"provider": "existing_runpod", "name": "Pod A", "pod_id": "podA1",
            "ssh_fingerprint": format!("SHA256:{}", "A".repeat(43)), "ssh_key_file": "C:\\keys\\id_ed25519"})
    }
    fn server_fields() -> Value {
        json!({"provider": "ssh_server", "name": "Lab server", "server_ssh_host": "gpu.example", "server_ssh_port": "22",
            "server_ssh_username": "researcher", "server_ssh_fingerprint": format!("SHA256:{}", "B".repeat(43)),
            "server_remote_root": "/home/researcher/factory", "server_ssh_key_file": "C:\\keys\\gpu_ed25519",
            "server_total_hourly_cost_usd": "0", "server_selected_gpu_uuids": "[\"GPU-11111111-2222-3333-4444-555555555555\"]"})
    }
    /// Settings as `read_settings(root, true)` returns them: every saved credential plus the public view.
    fn full_settings(mode: &str) -> Map<String, Value> {
        let mut settings = obj(json!({"auth_mode": mode, "MLFLOW_TRACKING_URI": "https://mlflow.invalid",
            "FACTORY_MLFLOW_ARTIFACT_LOCATION": "", "FACTORY_OPERATOR": "researcher",
            "MLFLOW_OIDC_TOKEN_URL": "https://sso.invalid/token", "MLFLOW_OIDC_CLIENT_ID": "mlflow",
            "pod_id": "legacy-pod", "provider": "existing_runpod", "S3_ENDPOINT_URL": "https://s3.invalid", "S3_REGION": "kr"}));
        for name in crate::SECRETS {
            settings.insert(name.into(), json!(synthetic(&name.to_ascii_lowercase())));
            settings.insert(format!("{name}_saved"), json!(true));
        }
        settings
    }
    fn sample_settings() -> Map<String, Value> {
        let mut settings = full_settings("token");
        settings.insert("MLFLOW_TRACKING_TOKEN".into(), json!(synthetic("token")));
        settings
    }
    fn sample_profiles() -> Value {
        json!([{"profile_id": "gpu-a", "fields": runpod_fields(), "RUNPOD_API_KEY_saved": true,
                "secrets": {"RUNPOD_API_KEY": synthetic("profile-a"), "HF_TOKEN": synthetic("smuggled")}},
               {"profile_id": "lab", "fields": server_fields(), "RUNPOD_API_KEY_saved": false, "secrets": {}}])
    }
    /// `graph_store.OWNER_ID`: `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` (full match).
    fn python_owner_id(value: &str) -> bool {
        let bytes = value.as_bytes();
        !bytes.is_empty() && bytes.len() <= 128 && bytes[0].is_ascii_alphanumeric()
            && bytes.iter().all(|c| c.is_ascii_alphanumeric() || b"_.:-".contains(c))
    }
    fn never() -> Result<Sidecar, String> { panic!("the sidecar must not start") }
    fn status_file(app: &Path) -> PathBuf { app.join("state").join("controller").join("broker.json") }

    /// A sidecar stdin stand-in that records what the broker wrote and when the broker closed it.
    struct Probe { written: Arc<Mutex<Vec<u8>>>, closed: Arc<AtomicBool> }
    impl Write for Probe {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            assert!(!self.closed.load(Ordering::SeqCst));
            self.written.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> { Ok(()) }
    }
    impl Drop for Probe {
        fn drop(&mut self) { self.closed.store(true, Ordering::SeqCst); }
    }

    #[test]
    fn controller_mode_requires_exactly_the_flag() {
        assert!(is_controller_mode(&args(&["C:\\Apps\\factory.exe", "--controller"])));
        for argv in [&[][..], &["factory.exe"], &["factory.exe", "--controller", "x"], &["factory.exe", "--Controller"],
            &["factory.exe", "--controller="], &["factory.exe", "controller"], &["factory.exe", " --controller"],
            &["factory.exe", "x", "--controller"], &["--controller"]] {
            assert!(!is_controller_mode(&args(argv)), "{argv:?} selected the broker");
        }
    }

    #[test]
    fn broker_and_sidecar_commands_have_fixed_arguments_and_no_environment() {
        let exe = Path::new("C:\\Program Files\\LLM Tuning Factory\\llm-tuning-factory-desktop.exe");
        let sidecar = Path::new("C:\\Program Files\\LLM Tuning Factory\\factory-bridge.exe");
        for (command, program) in [(broker_command(exe, true), exe), (broker_command(exe, false), exe), (sidecar_command(sidecar), sidecar)] {
            assert_eq!(command.get_program(), program.as_os_str());
            assert_eq!(command.get_args().collect::<Vec<_>>(), [OsStr::new(CONTROLLER_ARG)]);
            assert_eq!(command.get_envs().count(), 0, "a spawned command sets or removes environment variables");
            assert!(command.get_current_dir().is_none());
        }
        assert_eq!(broker_creation_flags(true), CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB);
        assert_eq!(broker_creation_flags(false), CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
        assert_eq!(SIDECAR_CREATION_FLAGS, CREATE_NO_WINDOW);
        // Only the bundled sidecar, resolved exactly like factory_action's (F1).
        let binary = sidecar_binary().unwrap();
        let name = binary.file_name().unwrap().to_string_lossy().into_owned();
        assert!(name == "factory-bridge.exe" || name == "factory-bridge-x86_64-pc-windows-msvc.exe", "{name}");
    }

    #[test]
    fn broker_start_line_is_strict() {
        let root = std::env::temp_dir().join("factory-app");
        let line = broker_start_line(&root).unwrap();
        assert!(line.ends_with('\n') && line.matches('\n').count() == 1);
        assert_eq!(serde_json::from_str::<Value>(&line).unwrap(), json!({"schema": BROKER_SCHEMA, "app_root": root}));
        assert_eq!(parse_broker_start(&line).unwrap(), root);
        assert_eq!(parse_broker_start(line.trim_end()).unwrap(), root);
        let long_root = root.join("a".repeat(MAX_APP_ROOT));
        let padded = format!("{}{}", json!({"schema": BROKER_SCHEMA, "app_root": root}), " ".repeat(MAX_BROKER_LINE));
        for bad in [json!({"schema": BROKER_SCHEMA, "app_root": root, "extra": 1}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": root, "RUNPOD_API_KEY": "x"}).to_string(),
            json!({"schema": "factory-broker/2", "app_root": root}).to_string(),
            json!({"schema": START_SCHEMA, "app_root": root}).to_string(),
            json!({"app_root": root}).to_string(), json!({"schema": BROKER_SCHEMA}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": "relative\\dir"}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": ""}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": "\\\\server\\share\\app"}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": "//server/share/app"}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": "\\/server/share/app"}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": "\\\\?\\C:\\app"}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": "\\\\.\\pipe\\app"}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": format!("{}\u{0}x", root.display())}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": format!("{}\nx", root.display())}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": root.join("..").join("x")}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": long_root}).to_string(),
            json!({"schema": BROKER_SCHEMA, "app_root": 12}).to_string(),
            json!([BROKER_SCHEMA]).to_string(), "not json".into(), String::new(), padded] {
            assert!(parse_broker_start(&bad).is_err(), "{bad:.120} was accepted");
        }
    }

    #[test]
    fn controller_message_carries_scoped_secrets_only() {
        for mode in ["", "oidc", "token", "none"] {
            let message = controller_message(Path::new("C:\\factory\\state"), "desktop.42.7", &full_settings(mode), &sample_profiles());
            let keys: BTreeSet<&str> = message.as_object().unwrap().keys().map(String::as_str).collect();
            assert_eq!(keys, BTreeSet::from(["schema", "state_root", "owner_id", "tracking", "profiles"]));
            assert_eq!(message["schema"], START_SCHEMA);
            assert_eq!(message["owner_id"], "desktop.42.7");
            assert_eq!(message["state_root"], "C:\\factory\\state");
            let tracking = message["tracking"].as_object().unwrap();
            assert!(tracking.keys().all(|name| CONTROLLER_TRACKING_FIELDS.contains(&name.as_str())), "{mode:?}: {tracking:?}");
            let expected: &[&str] = match mode {
                "none" => &[],
                "token" => &["MLFLOW_TRACKING_TOKEN"],
                _ => &["MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET"],
            };
            for name in crate::MLFLOW_CREDENTIALS {
                assert_eq!(tracking.contains_key(name), expected.contains(&name), "{mode:?}: {name}");
            }
            assert_eq!(tracking.get("auth_mode"), Some(&json!(mode)));
            assert_eq!(tracking.get("MLFLOW_TRACKING_URI"), Some(&json!("https://mlflow.invalid")));
            assert_eq!(tracking.get("FACTORY_OPERATOR"), Some(&json!("researcher")));
            let text = message.to_string();
            for name in ["RUNPOD_API_KEY", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN"] {
                assert!(!text.contains(&synthetic(&name.to_ascii_lowercase())), "{mode:?} leaked the legacy {name}");
            }
            assert!(!text.contains(&synthetic("smuggled")), "a profile carried a secret outside PROFILE_SECRETS");
            assert!(!text.contains("_saved") && !text.contains("S3_") && !text.contains("legacy-pod"), "{text}");
            // Each profile carries its own key only, and only its public fields.
            assert_eq!(message["profiles"], json!([
                {"profile_id": "gpu-a", "fields": runpod_fields(), "secrets": {"RUNPOD_API_KEY": synthetic("profile-a")}},
                {"profile_id": "lab", "fields": server_fields(), "secrets": {}}]));
        }
        // Non-string or missing values never become tracking values.
        let mut odd = full_settings("token");
        odd.insert("FACTORY_OPERATOR".into(), json!(12));
        odd.remove("MLFLOW_TRACKING_URI");
        let message = controller_message(Path::new("C:\\f\\state"), "desktop.1.2", &odd, &json!([]));
        assert!(message["tracking"].get("FACTORY_OPERATOR").is_none() && message["tracking"].get("MLFLOW_TRACKING_URI").is_none());
        assert_eq!(message["profiles"], json!([]));
    }

    #[test]
    fn controller_message_matches_the_pinned_contract_sample() {
        let message = controller_message(Path::new("C:\\factory\\state"), "desktop.4242.1760918400000000000",
            &sample_settings(), &sample_profiles());
        assert_eq!(message, serde_json::from_str::<Value>(CONTROLLER_MESSAGE_SAMPLE).unwrap());
    }

    #[test]
    fn owner_ids_match_the_python_pattern() {
        assert_eq!(owner_id(42, 7), "desktop.42.7");
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        for (pid, nanos) in [(0, 0), (u32::MAX, u128::MAX), (std::process::id(), now)] {
            let id = owner_id(pid, nanos);
            assert!(python_owner_id(&id), "{id}");
        }
        assert_ne!(owner_id(1, 1), owner_id(1, 2));
    }

    #[test]
    fn broker_writes_one_start_line_and_keeps_the_pipe_open_until_the_sidecar_exits() {
        let app = TempRoot::new("broker-serve");
        let (token, oidc, profile_key) = (synthetic("token"), synthetic("oidc"), synthetic("profile-a"));
        let others = [synthetic("legacy-runpod"), synthetic("s3"), synthetic("hf")];
        fs::write(app.path().join("settings.json"), json!({"auth_mode": "token", "MLFLOW_TRACKING_URI": "https://mlflow.invalid",
            "pod_id": "legacy-pod", "S3_REGION": "kr", "MLFLOW_OIDC_TOKEN_URL": "https://sso.invalid/token"}).to_string()).unwrap();
        let vault = MemoryVault::with(&[("MLFLOW_TRACKING_TOKEN", &token), ("MLFLOW_OIDC_CLIENT_SECRET", &oidc),
            ("RUNPOD_API_KEY", &others[0]), ("S3_SECRET_ACCESS_KEY", &others[1]), ("HF_TOKEN", &others[2])]);
        let mut fields = obj(runpod_fields());
        fields.insert("RUNPOD_API_KEY".into(), json!(profile_key));
        save_profile(app.path(), &vault, "gpu-a", &fields, None).unwrap();
        save_profile(app.path(), &vault, "lab", &obj(server_fields()), None).unwrap();
        vault.clear_reads();
        let written = Arc::new(Mutex::new(Vec::new()));
        let closed = Arc::new(AtomicBool::new(false));
        let mut spawned = 0;
        let mut spawn = || -> Result<Sidecar, String> {
            spawned += 1;
            let (seen, open) = (written.clone(), closed.clone());
            Ok(Sidecar {
                stdin: Box::new(Probe { written: written.clone(), closed: closed.clone() }),
                wait: Box::new(move || {
                    assert!(!open.load(Ordering::SeqCst), "the broker closed the pipe before the sidecar exited");
                    assert!(seen.lock().unwrap().ends_with(b"\n"), "the start line was not flushed before waiting");
                    7
                }),
            })
        };
        let line = broker_start_line(app.path()).unwrap();
        let code = serve_broker(&mut line.as_bytes(), &vault, "desktop.1.2", &mut spawn);
        assert_eq!(code, 7, "the broker must exit with the sidecar's code");
        assert_eq!(spawned, 1);
        assert!(closed.load(Ordering::SeqCst), "the pipe must close once the sidecar exited");
        let bytes = written.lock().unwrap().clone();
        assert_eq!(bytes.iter().filter(|byte| **byte == b'\n').count(), 1, "exactly one line");
        let message: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(message["state_root"], json!(app.path().join("state")));
        assert_eq!(message["owner_id"], "desktop.1.2");
        assert_eq!(message["tracking"], json!({"auth_mode": "token", "MLFLOW_TRACKING_URI": "https://mlflow.invalid",
            "MLFLOW_TRACKING_TOKEN": token}));
        assert_eq!(message["profiles"][0]["secrets"], json!({"RUNPOD_API_KEY": profile_key}));
        assert_eq!(message["profiles"][1]["secrets"], json!({}));
        let text = String::from_utf8(bytes).unwrap();
        for secret in others.iter().chain([&oidc]) { assert!(!text.contains(secret.as_str()), "an unscoped credential was sent"); }
        // Least privilege: the broker read only the MLflow secret its auth mode needs and the profile's own key.
        let reads: BTreeSet<String> = vault.read_names().into_iter().collect();
        assert_eq!(reads, BTreeSet::from(["MLFLOW_TRACKING_TOKEN".to_string(), profile_secret_entry("gpu-a", "RUNPOD_API_KEY")]));
        // The fake sidecar exited with 7, so the window learns why the controller is absent.
        let status: Value = serde_json::from_slice(&fs::read(status_file(app.path())).unwrap()).unwrap();
        assert_eq!(status["code"], "controller_failed");
    }

    #[test]
    fn a_sidecar_that_stops_with_an_error_leaves_a_public_reason() {
        let app = TempRoot::new("broker-exit");
        fs::write(app.path().join("settings.json"), json!({"auth_mode": "token", "MLFLOW_TRACKING_URI": "https://mlflow.invalid"})
            .to_string()).unwrap();
        let token = synthetic("token");
        let vault = MemoryVault::with(&[("MLFLOW_TRACKING_TOKEN", &token)]);
        let start = broker_start_line(app.path()).unwrap();
        // 0 is a drained stop and 3 a duplicate controller (`owner_held`, the other one keeps running): no reason.
        for (exit, expected) in [(2, Some("controller_start_refused")), (0, None), (4, Some("controller_settings_refused")),
            (3, None), (1, Some("controller_failed")), (7, Some("controller_failed")), (-1, Some("controller_failed"))] {
            let mut spawn = || -> Result<Sidecar, String> { Ok(Sidecar { stdin: Box::new(io::sink()), wait: Box::new(move || exit) }) };
            assert_eq!(serve_broker(&mut start.as_bytes(), &vault, "desktop.1.2", &mut spawn), exit);
            match expected {
                None => assert!(!status_file(app.path()).exists(), "exit {exit} left a failure status"),
                Some(code) => {
                    let status: Value = serde_json::from_slice(&fs::read(status_file(app.path())).unwrap()).unwrap();
                    let keys: BTreeSet<&str> = status.as_object().unwrap().keys().map(String::as_str).collect();
                    assert_eq!(keys, BTreeSet::from(["at", "code", "error"]), "exit {exit}");
                    assert_eq!(status["code"], code, "exit {exit}");
                    assert!(status["error"].as_str().unwrap().contains("컨트롤러"), "exit {exit}");
                    assert!(!status.to_string().contains(&token));
                }
            }
        }
    }

    #[test]
    fn broker_failures_before_the_spawn_exit_with_fixed_codes_and_public_status() {
        let app = TempRoot::new("broker-fail");
        let token = synthetic("token");
        let vault = MemoryVault::with(&[("MLFLOW_TRACKING_TOKEN", &token)]);
        // 10: a bad start line starts nothing and writes nothing.
        let long = "x".repeat(MAX_BROKER_LINE + 10);
        let extra = json!({"schema": BROKER_SCHEMA, "app_root": app.path(), "x": 1}).to_string();
        for line in ["", "garbage\n", extra.as_str(), long.as_str()] {
            assert_eq!(serve_broker(&mut line.as_bytes(), &vault, "desktop.1.2", &mut never), EXIT_BAD_START);
        }
        assert!(!app.path().join("state").exists());
        let start = broker_start_line(app.path()).unwrap();
        let check_status = |code: &str| {
            let status: Value = serde_json::from_slice(&fs::read(status_file(app.path())).unwrap()).unwrap();
            let keys: BTreeSet<&str> = status.as_object().unwrap().keys().map(String::as_str).collect();
            assert_eq!(keys, BTreeSet::from(["at", "code", "error"]));
            assert_eq!(status["code"], code);
            assert!(status["at"].as_f64().unwrap() > 1.0e9);
            assert!(!status.to_string().contains(&token));
            status["error"].as_str().unwrap().to_owned()
        };
        // 11: unreadable settings, an unavailable vault, a corrupt profile store or an oversized start message.
        let valid = json!({"auth_mode": "token", "MLFLOW_TRACKING_URI": "https://mlflow.invalid"}).to_string();
        fs::write(app.path().join("settings.json"), "{broken").unwrap();
        assert_eq!(serve_broker(&mut start.as_bytes(), &vault, "desktop.1.2", &mut never), EXIT_SETTINGS);
        let error = check_status("settings_unavailable");
        assert!(error.contains("설정"), "{error}");
        fs::write(app.path().join("settings.json"), &valid).unwrap();
        assert_eq!(serve_broker(&mut start.as_bytes(), &FailingVault, "desktop.1.2", &mut never), EXIT_SETTINGS);
        check_status("settings_unavailable");
        fs::write(app.path().join(crate::profiles::PROFILES_FILE), "[]").unwrap();
        assert_eq!(serve_broker(&mut start.as_bytes(), &vault, "desktop.1.2", &mut never), EXIT_SETTINGS);
        check_status("settings_unavailable");
        fs::remove_file(app.path().join(crate::profiles::PROFILES_FILE)).unwrap();
        let huge = json!({"auth_mode": "none", "MLFLOW_TRACKING_URI": format!("http://127.0.0.1/{}", "a".repeat(MAX_START_BYTES))});
        fs::write(app.path().join("settings.json"), huge.to_string()).unwrap();
        assert_eq!(serve_broker(&mut start.as_bytes(), &vault, "desktop.1.2", &mut never), EXIT_SETTINGS);
        check_status("start_too_large");
        // 12: the sidecar cannot be started.
        fs::write(app.path().join("settings.json"), &valid).unwrap();
        let mut failing = || -> Result<Sidecar, String> { Err("no sidecar".into()) };
        assert_eq!(serve_broker(&mut start.as_bytes(), &vault, "desktop.1.2", &mut failing), EXIT_SPAWN);
        check_status("sidecar_unavailable");
        // A later successful start removes the stale failure status.
        let mut started = || -> Result<Sidecar, String> { Ok(Sidecar { stdin: Box::new(io::sink()), wait: Box::new(|| 0) }) };
        assert_eq!(serve_broker(&mut start.as_bytes(), &vault, "desktop.1.2", &mut started), 0);
        assert!(!status_file(app.path()).exists());
    }

    #[test]
    fn breakaway_spawn_falls_back_only_on_access_denied() {
        let mut attempts = Vec::new();
        let spawned = spawn_breakaway_or_fallback(|breakaway| {
            attempts.push(breakaway);
            if breakaway { Err(io::Error::from_raw_os_error(5)) } else { Ok("spawned") }
        });
        assert_eq!(spawned.unwrap(), "spawned");
        assert_eq!(attempts, [true, false]);
        let mut attempts = Vec::new();
        let missing = spawn_breakaway_or_fallback(|breakaway| { attempts.push(breakaway); Err::<(), _>(io::Error::from_raw_os_error(2)) });
        assert!(missing.is_err());
        assert_eq!(attempts, [true], "only access denied (job without breakaway) may retry");
        let mut attempts = Vec::new();
        let denied = spawn_breakaway_or_fallback(|breakaway| { attempts.push(breakaway); Err::<(), _>(io::Error::from_raw_os_error(5)) });
        assert!(denied.is_err());
        assert_eq!(attempts, [true, false], "exactly one retry");
        let mut attempts = Vec::new();
        assert!(spawn_breakaway_or_fallback(|breakaway| { attempts.push(breakaway); Ok(()) }).is_ok());
        assert_eq!(attempts, [true]);
    }

    #[test]
    fn controller_start_spawns_only_when_the_status_says_absent_or_stale() {
        let status = |state: &str| json!({"status": "ok", "issues": [], "data": {"controller": {"state": state}}});
        assert_eq!(should_start(&status("absent")), Ok(true));
        assert_eq!(should_start(&status("stale")), Ok(true));
        assert_eq!(should_start(&status("running")), Ok(false));
        let missing = json!({"status": "invalid", "data": null,
            "issues": [{"code": "not_available", "path": "", "message": "이 기능은 아직 설치되지 않았습니다."}]});
        assert!(should_start(&missing).unwrap_err().contains("설치되지 않았습니다"));
        for unknown in [status("starting"), json!({"status": "ok", "issues": [], "data": {}}), json!({"status": "conflict"}), json!(null)] {
            assert!(should_start(&unknown).is_err(), "{unknown} would start a broker");
        }
    }
}
