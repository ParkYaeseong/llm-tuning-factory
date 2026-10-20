#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod broker;
mod profiles;

use profiles::{KeyringVault, Vault};
use serde_json::{json, Map, Value};
use std::{fs, io::Write, path::{Path, PathBuf}, process::{Command, Stdio}, sync::Mutex};
use tauri::Manager;

const SECRETS: [&str; 7] = ["RUNPOD_API_KEY", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN",
    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN"];
const FIELDS: [&str; 28] = [
    "MLFLOW_TRACKING_URI", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR",
    "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "pod_id", "ssh_key_file",
    "ssh_fingerprint", "RUNPOD_API_KEY", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN", "auth_mode",
    "S3_ENDPOINT_URL", "S3_REGION", "S3_ADDRESSING_STYLE", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN",
    // GPU server connection: public strings validated by the sidecar. The key file is a local path, not key content.
    "provider", "server_ssh_host", "server_ssh_port", "server_ssh_username", "server_ssh_fingerprint",
    "server_remote_root", "server_ssh_key_file", "server_total_hourly_cost_usd", "server_selected_gpu_uuids",
];
// OIDC values first, then the issued token: token mode withholds the first three, "none" all four.
const MLFLOW_CREDENTIALS: [&str; 4] = ["MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
    "MLFLOW_TRACKING_TOKEN"];
// The least-privilege workflow channel (Task 9 plan D8): exactly desktop_workflows.WORKFLOW_ACTIONS.
const WORKFLOW_ACTIONS: [&str; 33] = [
    "projects_list", "project_create", "project_get", "project_save", "project_import_legacy",
    "recipes_list", "recipe_get", "recipe_save", "recipe_clone",
    "dataset_import_source", "dataset_import_preview", "dataset_import_register",
    "dataset_split_preview", "dataset_split_commit",
    "executor_profile_inspect", "model_root_snapshot", "model_roots_list", "benchmark_save", "benchmarks_list",
    "graph_validate", "graph_plan", "graph_plan_get",
    "graph_submit", "graph_runs_list", "graph_run_get", "graph_pause", "graph_resume", "graph_cancel",
    "tracking_rearm", "executor_profile_edit_check",
    "controller_status", "controller_stop",
    "model_root_targets",
];
// The only workflow actions that receive a credential: that profile's RunPod key, and the legacy HF token (the
// config/tokenizer snapshot and 9.7's header-only target read).
const PROFILE_SCOPED_ACTIONS: [&str; 1] = ["executor_profile_inspect"];
const HF_SCOPED_ACTIONS: [&str; 2] = ["model_root_snapshot", "model_root_targets"];
// Controller lifecycle requests never touch Credential Manager and survive unreadable settings or profile files, so a
// broken vault or file can never block stopping (or polling) the background controller.
const VAULT_FREE_ACTIONS: [&str; 2] = ["controller_status", "controller_stop"];
struct SaveLock(Mutex<()>);

fn app_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app.path().app_data_dir().map_err(|_| "앱 저장 폴더를 찾을 수 없습니다.")?;
    fs::create_dir_all(&root).map_err(|_| "앱 저장 폴더를 만들 수 없습니다.")?;
    Ok(root)
}

fn entry(name: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(profiles::SERVICE, name).map_err(|_| "Windows 자격 증명 저장소 오류".into())
}

/// `settings.json` as an object without any credential name (legacy or hand-edited plaintext is never returned).
fn read_settings_file(root: &Path) -> Result<Value, String> {
    let path = root.join("settings.json");
    let mut value = if path.exists() {
        serde_json::from_slice::<Value>(&fs::read(path).map_err(|_| "설정을 읽을 수 없습니다.")?)
            .map_err(|_| "설정 파일이 손상되었습니다.")?
    } else { json!({}) };
    if !value.is_object() { return Err("설정 파일이 손상되었습니다.".into()); }
    for name in SECRETS { value.as_object_mut().unwrap().remove(name); }
    Ok(value)
}

/// The public view with `<name>_saved` flags, or with the saved credentials (`secrets`, MLflow filtered by auth mode).
fn read_settings_from(root: &Path, vault: &dyn Vault, secrets: bool) -> Result<Value, String> {
    let mut value = read_settings_file(root)?;
    for name in SECRETS {
        if let Some(secret) = vault.get(name)? {
            value[format!("{name}_saved")] = json!(true);
            if secrets { value[name] = json!(secret); }
        }
    }
    filter_mlflow_auth(value.as_object_mut().unwrap(), secrets);
    Ok(value)
}

fn read_settings(root: &Path, secrets: bool) -> Result<Value, String> {
    read_settings_from(root, &KeyringVault, secrets)
}

/// Only the chosen MLflow method's values reach the sidecar (`secrets`). The public view used by the form
/// keeps every field and the vault keeps every credential, so switching the method back reuses them.
fn filter_mlflow_auth(settings: &mut Map<String, Value>, secrets: bool) {
    if !secrets { return; }
    let withheld: &[&str] = match settings.get("auth_mode").and_then(Value::as_str).unwrap_or("") {
        "token" => &MLFLOW_CREDENTIALS[..3],
        "none" => &MLFLOW_CREDENTIALS,
        _ => &MLFLOW_CREDENTIALS[3..],
    };
    for name in withheld { settings.remove(*name); }
}

#[tauri::command]
fn settings_get(app: tauri::AppHandle) -> Result<Value, String> {
    read_settings(&app_root(&app)?, false)
}

const KEY_FILE_FIELDS: [&str; 2] = ["ssh_key_file", "server_ssh_key_file"];
// Pasted OpenSSH/PEM or PuTTY key content (= desktop_workflows.KEY_MARKERS).
const KEY_MARKERS: [&str; 3] = ["PRIVATE KEY", "PuTTY-User-Key-File-", "Private-Lines:"];
const PASTED_KEY_REFUSED: &str = "SSH 개인키 파일에는 키 내용이 아니라 키 파일 경로를 입력하세요.";
const PPK_REFUSED: &str = "PuTTY .ppk 키는 지원하지 않습니다. OpenSSH 형식(암호 없는 키)으로 변환한 키 파일을 선택하세요.";

fn key_content(text: &str) -> bool { KEY_MARKERS.iter().any(|marker| text.contains(marker)) }

/// Pasted OpenSSH/PEM or PuTTY (.ppk) key content in a key-file path field.
fn pasted_key(name: &str, value: &Value) -> bool {
    KEY_FILE_FIELDS.contains(&name) && value.as_str().is_some_and(key_content)
}

/// A PuTTY .ppk path in a key-file field. Windows drops trailing dots and spaces from file names, so "key.ppk."
/// names the same .ppk file.
fn ppk_path(name: &str, value: &Value) -> bool {
    KEY_FILE_FIELDS.contains(&name) && value.as_str().is_some_and(|path|
        path.trim_end_matches(|c: char| c == '.' || c.is_whitespace()).to_ascii_lowercase().ends_with(".ppk"))
}

/// Empty, or a JSON array of at most 8 strings.
fn valid_gpu_selection(selection: &str) -> bool {
    selection.is_empty() || matches!(serde_json::from_str::<Value>(selection),
        Ok(Value::Array(items)) if items.len() <= 8 && items.iter().all(Value::is_string))
}

fn merge_public_settings(existing: Value, input: &Map<String, Value>) -> Result<Map<String, Value>, String> {
    if input.keys().any(|key| !FIELDS.contains(&key.as_str())) {
        return Err("허용하지 않은 설정 항목".into());
    }
    for value in input.values() {
        let text = value.as_str().ok_or("설정은 문자열이어야 합니다.")?;
        if text.len() > 2048 { return Err("설정값이 너무 깁니다.".into()); }
    }
    // settings.json is plaintext: refuse submitted key content where only a path belongs. A value stored
    // before this check is dropped below instead of blocking unrelated saves (S3, HF, the other provider).
    if input.iter().any(|(name, value)| pasted_key(name, value)) {
        return Err(PASTED_KEY_REFUSED.into());
    }
    // The sidecar's SSH client reads OpenSSH keys without a passphrase or agent, never PuTTY files.
    if input.iter().any(|(name, value)| ppk_path(name, value)) {
        return Err(PPK_REFUSED.into());
    }
    let previous = existing.as_object().ok_or("설정 파일이 손상되었습니다.")?;
    let mut public: Map<String, Value> = previous.iter()
        .filter(|(name, value)| FIELDS.contains(&name.as_str()) && !SECRETS.contains(&name.as_str()) && value.is_string()
            && !pasted_key(name, value))
        .map(|(name, value)| (name.clone(), value.clone())).collect();
    for (name, value) in input {
        if !SECRETS.contains(&name.as_str()) { public.insert(name.clone(), value.clone()); }
    }
    let text = |name: &str| public.get(name).and_then(Value::as_str).unwrap_or("");
    if !["", "existing_runpod", "ssh_server"].contains(&text("provider")) {
        return Err("GPU 연결 대상은 RunPod 또는 GPU 서버여야 합니다.".into());
    }
    if !["", "oidc", "token", "none"].contains(&text("auth_mode")) {
        return Err("MLflow 인증 방식은 기관 인증·발급 토큰·인증 없음 중 하나여야 합니다.".into());
    }
    if !valid_gpu_selection(text("server_selected_gpu_uuids")) {
        return Err("선택한 GPU 목록은 GPU UUID 문자열 최대 8개의 JSON 배열이어야 합니다.".into());
    }
    if let Some(endpoint) = public.get("S3_ENDPOINT_URL").and_then(Value::as_str) {
        if !endpoint.is_empty() {
            let url = tauri::Url::parse(endpoint).map_err(|_| "S3 endpoint는 인증값 없는 HTTPS 기본 주소여야 합니다.")?;
            let authority_and_path = endpoint.split_once("://").map(|(_, rest)| rest).unwrap_or("");
            let raw_path = authority_and_path.find('/').map(|index| &authority_and_path[index..]).unwrap_or("");
            if (!raw_path.is_empty() && raw_path != "/") || endpoint.trim() != endpoint || endpoint.chars().any(char::is_control) || url.scheme() != "https" || url.host_str().is_none()
                || !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some()
                || url.path() != "/" || authority_and_path.split('/').next().unwrap_or("").contains('@') {
                return Err("S3 endpoint는 인증값·경로·query·fragment 없는 HTTPS 기본 주소여야 합니다.".into());
            }
        }
    }
    Ok(public)
}

#[tauri::command]
fn settings_save(app: tauri::AppHandle, values: Value, lock: tauri::State<SaveLock>) -> Result<Value, String> {
    let _guard = lock.0.lock().map_err(|_| "설정 잠금 오류")?;
    let root = app_root(&app)?;
    let input = values.as_object().ok_or("잘못된 설정")?;
    let previous = root.join("settings.json");
    let existing = if previous.exists() {
        serde_json::from_slice::<Value>(&fs::read(previous).map_err(|_| "설정을 읽을 수 없습니다.")?)
            .map_err(|_| "설정 파일이 손상되었습니다.")?
    } else { json!({}) };
    let public = merge_public_settings(existing, input)?;
    // Validate all fields before writing any credential. Empty secret inputs preserve saved values.
    for (name, value) in input {
        let text = value.as_str().unwrap();
        if SECRETS.contains(&name.as_str()) && !text.is_empty() {
            entry(name)?.set_password(text).map_err(|_| "인증값 보안 저장 실패")?;
        }
    }
    let temporary = root.join("settings.tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(&public).unwrap()).map_err(|_| "설정 저장 실패")?;
    // Windows rename cannot replace an existing file. Keep credentials exclusively in Credential Manager.
    fs::copy(&temporary, root.join("settings.json")).map_err(|_| "설정 저장 실패")?;
    fs::remove_file(temporary).ok();
    read_settings(&root, false)
}

#[tauri::command]
fn forget_credentials(app: tauri::AppHandle) -> Result<Value, String> {
    // Resolve the app folder without creating it: forgetting never creates files or folders.
    forget_all_credentials(|| app.path().app_data_dir().map_err(|_| "앱 저장 폴더를 찾을 수 없습니다.".to_owned()),
        &KeyringVault)
}

/// The seven connection credentials first, failing as before; then, best effort, every executor profile key the
/// profile file names. A profile-side problem is reported in the result and never fails the legacy part, so the
/// window still refreshes after "인증값 삭제".
fn forget_all_credentials(root: impl FnOnce() -> Result<PathBuf, String>, vault: &dyn Vault) -> Result<Value, String> {
    for name in SECRETS { vault.delete(name)?; }
    Ok(match root().and_then(|root| profiles::forget_profile_secrets(&root, vault)) {
        Ok(()) => json!({"profile_credentials_forgotten": true}),
        Err(error) => json!({"profile_credentials_forgotten": false,
            "profile_warning": format!("연결 인증값은 삭제했지만 실행 프로필 인증값을 모두 삭제했는지 확인하지 못했습니다. ({error})")}),
    })
}

/// The legacy one-request envelope, exactly as `factory_action` always built it.
fn legacy_request(root: &Path, settings: Value, action: &str, data: Value) -> Value {
    json!({"root": root.join("state"), "settings": settings, "action": action, "data": data})
}

#[tauri::command]
async fn pick_file(kind: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut dialog = rfd::FileDialog::new();
        if kind == "dataset" { dialog = dialog.add_filter("학습 데이터", &["json", "jsonl"]); }
        dialog.pick_file().map(|p| p.to_string_lossy().into_owned())
    }).await.map_err(|_| "파일 선택 오류".into())
}

fn allowed(action: &str) -> bool {
    matches!(action, "catalog" | "save_profile" | "dataset" | "plan" | "submit" | "execute" |
        "jobs" | "logs" | "cancel" | "recover" | "reconcile" | "resume" | "connection" |
        "pod" | "runtime_check" | "runtime_prepare" | "live_logs" | "history" | "compare" | "model_revision" |
        "dataset_preview" | "job_progress" | "capabilities" | "s3_list" | "s3_head" | "s3_import" |
        "model_search" | "model_inspect" | "model_import" |
        "server_inspect" | "server_facts" | "runtime_reservations" | "runtime_release" | "tutorial_sample")
}

#[tauri::command]
async fn factory_action(app: tauri::AppHandle, action: String, data: Value) -> Result<Value, String> {
    if !allowed(&action) { return Err("허용하지 않은 작업".into()); }
    let root = app_root(&app)?;
    let settings = read_settings(&root, true)?;
    tauri::async_runtime::spawn_blocking(move || {
        run_sidecar(&legacy_request(&root, settings, &action, data))
    }).await.map_err(|_| "엔진 작업 오류")?
}

/// The one-request sidecar: no argument, no added environment; credentials are only sent through stdin.
fn request_command(binary: &Path) -> Command {
    let mut command = Command::new(binary);
    command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW; credentials are only sent through stdin.
    }
    command
}

/// One request to a fresh sidecar (extracted from `factory_action` unchanged): one JSON line in, then
/// `{"ok": true, "result"}` → `Ok(result)` or `{"ok": false, "error"}` → `Err(error)`.
fn run_sidecar(request: &Value) -> Result<Value, String> {
    let mut child = request_command(&broker::sidecar_binary()?).spawn()
        .map_err(|_| "학습 엔진을 실행할 수 없습니다. 설치 파일을 확인해 주세요.")?;
    {
        let mut stdin = child.stdin.take().ok_or("엔진 입력 연결 오류")?;
        writeln!(stdin, "{}", request).map_err(|_| "엔진 요청 전송 실패")?;
    }
    let output = child.wait_with_output().map_err(|_| "엔진 응답 오류")?;
    let response: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "엔진 응답을 해석할 수 없습니다.")?;
    if response["ok"] == true { Ok(response["result"].clone()) }
    else { Err(response["error"].as_str().unwrap_or("실행 오류").to_owned()) }
}

fn workflow_allowed(action: &str) -> bool { WORKFLOW_ACTIONS.contains(&action) }

/// The settings view the workflow channel accepts: saved public strings without key content, and `*_saved` flags.
fn workflow_settings(public: &Value) -> Map<String, Value> {
    let saved: Vec<String> = SECRETS.iter().map(|name| format!("{name}_saved")).collect();
    public.as_object().into_iter().flatten()
        .filter(|(name, value)| (FIELDS.contains(&name.as_str()) && !SECRETS.contains(&name.as_str())
                && value.as_str().is_some_and(|text| !key_content(text)))
            || (saved.contains(name) && **value == json!(true)))
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect()
}

/// D8: only `executor_profile_inspect` (that stored RunPod profile's own key), `model_root_snapshot` and
/// `model_root_targets` (the legacy HF token) receive a credential.
fn scoped_secrets(root: &Path, vault: &dyn Vault, action: &str, data: &Value) -> Result<Map<String, Value>, String> {
    let mut secrets = Map::new();
    if PROFILE_SCOPED_ACTIONS.contains(&action) {
        if let Some(id) = data.get("profile_id").and_then(Value::as_str) {
            if let Some(key) = profiles::profile_secret(root, vault, id)? { secrets.insert("RUNPOD_API_KEY".into(), json!(key)); }
        }
    } else if HF_SCOPED_ACTIONS.contains(&action) {
        if let Some(token) = vault.get("HF_TOKEN")? { secrets.insert("HF_TOKEN".into(), json!(token)); }
    }
    Ok(secrets)
}

fn workflow_request(root: &Path, public_settings: Value, public_profiles: Value, secrets: Map<String, Value>,
                    action: &str, data: Value) -> Value {
    json!({"channel": "workflow", "root": root.join("state"), "settings": public_settings,
           "profiles": public_profiles, "secrets": secrets, "action": action, "data": data})
}

fn build_workflow_request(root: &Path, vault: &dyn Vault, action: &str, data: Value) -> Result<Value, String> {
    if !workflow_allowed(action) { return Err("허용하지 않은 작업".into()); }
    if !data.is_object() { return Err("잘못된 작업 요청".into()); }
    if VAULT_FREE_ACTIONS.contains(&action) {
        // Stopping needs nothing; polling sends the public files as far as they are readable, without credential flags.
        let (settings, profiles) = match action {
            "controller_status" => (read_settings_file(root).map(|public| workflow_settings(&public)).unwrap_or_default(),
                                    profiles::public_profiles(root).unwrap_or_default()),
            _ => (Map::new(), Vec::new()),
        };
        return Ok(workflow_request(root, Value::Object(settings), Value::Array(profiles), Map::new(), action, data));
    }
    let settings = workflow_settings(&read_settings_from(root, vault, false)?);
    let mut listed = profiles::read_profiles(root, vault, false)?;
    let secrets = scoped_secrets(root, vault, action, &data)?;
    Ok(workflow_request(root, Value::Object(settings), listed["profiles"].take(), secrets, action, data))
}

fn workflow_call(root: &Path, vault: &dyn Vault, action: &str, data: Value) -> Result<Value, String> {
    run_sidecar(&build_workflow_request(root, vault, action, data)?)
}

#[tauri::command]
async fn workflow_action(app: tauri::AppHandle, action: String, data: Value) -> Result<Value, String> {
    if !workflow_allowed(&action) { return Err("허용하지 않은 작업".into()); }
    let root = app_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || workflow_call(&root, &KeyringVault, &action, data))
        .await.map_err(|_| "엔진 작업 오류")?
}

#[tauri::command]
fn executor_profiles_get(app: tauri::AppHandle) -> Result<Value, String> {
    profiles::read_profiles(&app_root(&app)?, &KeyringVault, false)
}

fn edit_check(root: &Path, id: &str) -> Result<Value, String> {
    workflow_call(root, &KeyringVault, profiles::EDIT_CHECK_ACTION, json!({"profile_id": id}))
}

#[tauri::command]
async fn executor_profile_save(app: tauri::AppHandle, profile_id: String, values: Value,
                               copy_legacy_credential: Option<bool>) -> Result<Value, String> {
    let root = app_root(&app)?;
    let input = values.as_object().cloned().ok_or("잘못된 실행 프로필")?;
    tauri::async_runtime::spawn_blocking(move || {
        let lock = app.state::<SaveLock>();
        profiles::save_profile_checked(&root, &KeyringVault, &profile_id, &input, copy_legacy_credential == Some(true),
            &mut |id| edit_check(&root, id), &lock.0)
    }).await.map_err(|_| "엔진 작업 오류")?
}

#[tauri::command]
async fn executor_profile_delete(app: tauri::AppHandle, profile_id: String) -> Result<Value, String> {
    let root = app_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let lock = app.state::<SaveLock>();
        profiles::delete_profile_checked(&root, &KeyringVault, &profile_id, &mut |id| edit_check(&root, id), &lock.0)
    }).await.map_err(|_| "엔진 작업 오류")?
}

/// Starts the headless broker (this executable, exactly `--controller`) and hands it the one non-secret start line.
/// The window never waits for it: the broker and its controller outlive the GUI (D1, D3).
fn start_broker(root: &Path) -> Result<(), String> {
    let line = broker::broker_start_line(root)?;
    let exe = std::env::current_exe().map_err(|_| "앱 경로 오류")?;
    let mut child = broker::spawn_breakaway_or_fallback(|breakaway| broker::broker_command(&exe, breakaway).spawn())
        .map_err(|_| "백그라운드 컨트롤러를 시작할 수 없습니다.")?;
    let mut stdin = child.stdin.take().ok_or("백그라운드 컨트롤러 입력 연결 오류")?;
    stdin.write_all(line.as_bytes()).and_then(|()| stdin.flush())
        .map_err(|_| "백그라운드 컨트롤러 시작 요청 전송 실패")?;
    Ok(())
}

#[tauri::command]
async fn controller_start(app: tauri::AppHandle) -> Result<Value, String> {
    let root = app_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let status = workflow_call(&root, &KeyringVault, "controller_status", json!({}))?;
        if !broker::should_start(&status)? { return Ok(json!({"started": false, "reason": "already_running"})); }
        start_broker(&root)?;
        Ok(json!({"started": true}))
    }).await.map_err(|_| "엔진 작업 오류")?
}

#[tauri::command]
async fn controller_status(app: tauri::AppHandle) -> Result<Value, String> {
    workflow_action(app, "controller_status".into(), json!({})).await
}

#[tauri::command]
async fn controller_stop(app: tauri::AppHandle) -> Result<Value, String> {
    workflow_action(app, "controller_stop".into(), json!({})).await
}

fn main() {
    // Headless broker (D1): exactly [exe, "--controller"], decided before any window or WebView exists.
    let args: Vec<String> = std::env::args_os().map(|arg| arg.to_string_lossy().into_owned()).collect();
    if broker::is_controller_mode(&args) { std::process::exit(broker::run_broker(&KeyringVault)); }
    tauri::Builder::default()
        .manage(SaveLock(Mutex::new(())))
        .invoke_handler(tauri::generate_handler![settings_get, settings_save, forget_credentials, pick_file, factory_action,
            workflow_action, executor_profiles_get, executor_profile_save, executor_profile_delete, controller_start,
            controller_status, controller_stop])
        .run(tauri::generate_context!())
        .expect("Windows application startup failed");
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn partial_settings_save_preserves_other_connections_and_excludes_all_secrets() {
        let input = json!({"S3_REGION": "kr-standard", "S3_SECRET_ACCESS_KEY": "new-secret"});
        let existing = json!({"pod_id": "borrowed", "MLFLOW_TRACKING_URI": "https://tracking.example", "S3_ACCESS_KEY_ID": "legacy-plaintext", "unknown": "discard"});
        let result = merge_public_settings(existing, input.as_object().unwrap()).unwrap();
        assert_eq!(result.get("pod_id"), Some(&json!("borrowed")));
        assert_eq!(result.get("MLFLOW_TRACKING_URI"), Some(&json!("https://tracking.example")));
        assert_eq!(result.get("S3_REGION"), Some(&json!("kr-standard")));
        for name in SECRETS { assert!(!result.contains_key(name)); }
        assert!(!result.contains_key("unknown"));
        assert!(merge_public_settings(json!({}), json!({"arbitrary": "secret"}).as_object().unwrap()).is_err());
        assert!(merge_public_settings(json!({}), json!({"S3_REGION": 12}).as_object().unwrap()).is_err());
    }
    #[test]
    fn s3_endpoint_only_accepts_https_origins_without_secrets() {
        for endpoint in ["https://storage.example?X-Amz-Signature=synthetic-secret", "https://synthetic-user:synthetic-secret@storage.example", "https://user@storage.example", "https://storage.example/bucket", "https://storage.example/a/..", "https://storage.example/#secret", "http://localhost:9000", "https:///", "not-a-url"] {
            let input = json!({"S3_ENDPOINT_URL": endpoint, "S3_SECRET_ACCESS_KEY": "do-not-save"});
            assert!(merge_public_settings(json!({}), input.as_object().unwrap()).is_err(), "unsafe S3 endpoint was accepted");
        }
        for endpoint in ["", "https://storage.example", "https://storage.example:9443/"] {
            let input = json!({"S3_ENDPOINT_URL": endpoint});
            assert!(merge_public_settings(json!({}), input.as_object().unwrap()).is_ok());
        }
    }
    #[test]
    fn only_named_operations_are_available() {
        assert!(allowed("plan"));
        assert!(allowed("capabilities"));
        assert!(allowed("s3_list"));
        assert!(allowed("s3_head"));
        assert!(allowed("s3_import"));
        assert!(allowed("tutorial_sample"));
        assert!(SECRETS.contains(&"S3_ACCESS_KEY_ID"));
        assert!(SECRETS.contains(&"S3_SECRET_ACCESS_KEY"));
        assert!(SECRETS.contains(&"S3_SESSION_TOKEN"));
        assert!(FIELDS.contains(&"S3_ENDPOINT_URL"));
        assert!(!allowed("shell"));
        assert!(!allowed("pod-start"));
        assert!(!allowed("pod-stop"));
    }
    #[test]
    fn hf_metadata_actions_are_explicit_and_token_is_never_public() {
        for action in ["model_search", "model_inspect", "model_import"] { assert!(allowed(action)); }
        let result = merge_public_settings(json!({"pod_id":"assigned"}),
            json!({"HF_TOKEN":"synthetic-token"}).as_object().unwrap()).unwrap();
        assert!(!result.contains_key("HF_TOKEN"));
        assert_eq!(result.get("pod_id"), Some(&json!("assigned")));
        assert!(SECRETS.contains(&"HF_TOKEN"));
    }
    const SERVER_FIELDS: [&str; 8] = ["server_ssh_host", "server_ssh_port", "server_ssh_username",
        "server_ssh_fingerprint", "server_remote_root", "server_ssh_key_file", "server_total_hourly_cost_usd",
        "server_selected_gpu_uuids"];
    fn merge(input: Value) -> Result<Map<String, Value>, String> {
        merge_public_settings(json!({"pod_id": "assigned", "ssh_fingerprint": "SHA256:pod"}), input.as_object().unwrap())
    }
    #[test]
    fn server_settings_are_public_fields_and_keep_the_inactive_pod_settings() {
        for name in SERVER_FIELDS.iter().chain(["provider"].iter()) {
            assert!(FIELDS.contains(name), "{name} must be a saved field");
            assert!(!SECRETS.contains(name), "{name} is not a credential");
        }
        let result = merge(json!({"provider": "ssh_server", "server_ssh_host": "gpu.example", "server_ssh_port": "2222",
            "server_ssh_username": "researcher", "server_ssh_fingerprint": format!("SHA256:{}", "A".repeat(43)),
            "server_remote_root": "/home/researcher/factory", "server_ssh_key_file": "C:\\keys\\id_ed25519",
            "server_total_hourly_cost_usd": "0", "server_selected_gpu_uuids": "[\"GPU-11111111-2222-3333-4444-555555555555\"]",
            "RUNPOD_API_KEY": "", "MLFLOW_OIDC_CLIENT_SECRET": "synthetic-secret"})).unwrap();
        for name in SERVER_FIELDS { assert!(result.contains_key(name), "{name} was not saved"); }
        assert_eq!(result.get("provider"), Some(&json!("ssh_server")));
        assert_eq!(result.get("pod_id"), Some(&json!("assigned")));
        assert_eq!(result.get("ssh_fingerprint"), Some(&json!("SHA256:pod")));
        for name in SECRETS { assert!(!result.contains_key(name)); }
    }
    #[test]
    fn provider_auth_mode_and_gpu_selection_are_validated() {
        let nine = format!("[{}]", vec!["\"GPU-x\""; 9].join(","));
        for input in [json!({"provider": "runpod"}), json!({"provider": "SSH_SERVER"}), json!({"provider": " ssh_server"}),
            json!({"auth_mode": "basic"}), json!({"auth_mode": "NONE"}), json!({"server_selected_gpu_uuids": "GPU-1"}),
            json!({"server_selected_gpu_uuids": "{\"uuid\":\"GPU-1\"}"}), json!({"server_selected_gpu_uuids": "[1]"}),
            json!({"server_selected_gpu_uuids": "[\"GPU-1\""}), json!({"server_selected_gpu_uuids": "null"}),
            json!({"server_selected_gpu_uuids": nine})] {
            let error = merge(input.clone()).expect_err(&format!("{input} was accepted"));
            assert!(!error.is_empty());
        }
        let eight = format!("[{}]", vec!["\"GPU-x\""; 8].join(","));
        for input in [json!({"provider": ""}), json!({"provider": "existing_runpod"}), json!({"provider": "ssh_server"}),
            json!({"auth_mode": ""}), json!({"auth_mode": "oidc"}), json!({"auth_mode": "token"}), json!({"auth_mode": "none"}),
            json!({"server_selected_gpu_uuids": ""}), json!({"server_selected_gpu_uuids": "[]"}),
            json!({"server_selected_gpu_uuids": eight})] {
            assert!(merge(input.clone()).is_ok(), "{input} was refused");
        }
    }
    fn mlflow_settings(mode: &str) -> Map<String, Value> {
        json!({"auth_mode": mode, "MLFLOW_TRACKING_URI": "http://127.0.0.1:5000", "MLFLOW_OIDC_TOKEN_URL": "https://sso.example/token",
            "MLFLOW_OIDC_CLIENT_ID": "mlflow", "MLFLOW_OIDC_CLIENT_SECRET": "synthetic-secret", "MLFLOW_TRACKING_TOKEN": "synthetic-token",
            "MLFLOW_OIDC_CLIENT_SECRET_saved": true, "MLFLOW_TRACKING_TOKEN_saved": true}).as_object().unwrap().clone()
    }
    #[test]
    fn mlflow_auth_mode_controls_which_credentials_reach_the_sidecar() {
        const OIDC: [&str; 3] = ["MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET"];
        let mut none = mlflow_settings("none");
        filter_mlflow_auth(&mut none, true);
        for name in OIDC.iter().chain(["MLFLOW_TRACKING_TOKEN"].iter()) { assert!(!none.contains_key(*name), "none kept {name}"); }
        assert_eq!(none.get("MLFLOW_TRACKING_URI"), Some(&json!("http://127.0.0.1:5000")));
        // Saved credentials stay in the vault; only the flags remain visible.
        assert_eq!(none.get("MLFLOW_OIDC_CLIENT_SECRET_saved"), Some(&json!(true)));
        assert_eq!(none.get("MLFLOW_TRACKING_TOKEN_saved"), Some(&json!(true)));
        let mut token = mlflow_settings("token");
        filter_mlflow_auth(&mut token, true);
        for name in OIDC { assert!(!token.contains_key(name), "token kept {name}"); }
        assert_eq!(token.get("MLFLOW_TRACKING_TOKEN"), Some(&json!("synthetic-token")));
        for mode in ["oidc", ""] {
            let mut sidecar = mlflow_settings(mode);
            filter_mlflow_auth(&mut sidecar, true);
            assert!(!sidecar.contains_key("MLFLOW_TRACKING_TOKEN"));
            assert_eq!(sidecar.get("MLFLOW_OIDC_CLIENT_SECRET"), Some(&json!("synthetic-secret")));
            assert_eq!(sidecar.get("MLFLOW_OIDC_TOKEN_URL"), Some(&json!("https://sso.example/token")));
        }
        let mut legacy = mlflow_settings("oidc");
        legacy.remove("auth_mode");
        filter_mlflow_auth(&mut legacy, true);
        assert!(!legacy.contains_key("MLFLOW_TRACKING_TOKEN"));
        assert!(legacy.contains_key("MLFLOW_OIDC_CLIENT_SECRET"));
    }
    #[test]
    fn public_settings_keep_every_mlflow_field_so_switching_back_reuses_them() {
        // settings_get/settings_save feed the form; dropping the OIDC URL or client ID there would let a
        // none → oidc round trip overwrite the saved values with the form defaults.
        for mode in ["", "oidc", "token", "none"] {
            let mut public = mlflow_settings(mode);
            filter_mlflow_auth(&mut public, false);
            assert_eq!(public, mlflow_settings(mode), "public view changed for {mode:?}");
        }
    }
    /// A fake PEM header such as the OpenSSH or RSA one, assembled at runtime so no source file contains a
    /// header the release secret scan (scripts/check_secrets.py) would report.
    fn fake_key_header(kind: &str) -> String {
        format!("-----BEGIN {kind}{}-----", "PRIVATE KEY")
    }
    #[test]
    fn key_file_settings_refuse_pasted_private_keys() {
        for name in ["ssh_key_file", "server_ssh_key_file"] {
            for pasted in [format!("{}\nb3BlbnNzaC1rZXktdjE=", fake_key_header("OPENSSH ")), fake_key_header("RSA "),
                "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none".into(), "Public-Lines: 2\nAAAA\nPrivate-Lines: 1\nAAAA".into()] {
                let error = merge(json!({name: pasted})).expect_err("a pasted private key was accepted");
                assert!(error.contains("파일 경로"), "{error}");
            }
            assert!(merge(json!({name: "C:\\Users\\researcher\\.ssh\\id_ed25519"})).is_ok());
        }
    }
    #[test]
    fn key_file_settings_refuse_putty_ppk_paths() {
        // paramiko reads OpenSSH keys without a passphrase or agent; a .ppk path would only fail at connection time.
        for name in ["ssh_key_file", "server_ssh_key_file"] {
            // Windows drops trailing dots and spaces from file names, so "server.ppk." opens server.ppk.
            for path in ["C:\\Users\\researcher\\keys\\server.ppk", "C:\\keys\\SERVER.PPK", "D:\\keys\\gpu.Ppk",
                "C:\\keys\\server.ppk.", "C:\\keys\\server.ppk. .", "C:\\keys\\server.PPK.. "] {
                let error = merge(json!({name: path})).expect_err("a PuTTY .ppk path was accepted");
                assert!(error.contains("PuTTY .ppk") && error.contains("OpenSSH"), "{error}");
            }
            for path in ["C:\\keys\\id_ed25519", "C:\\keys\\ppk\\id_rsa", "C:\\keys\\server.ppk.pem"] {
                assert!(merge(json!({name: path})).is_ok(), "{path} was refused");
            }
        }
        // Only submitted values are validated: a stored .ppk path does not block an unrelated save.
        let stored = json!({"ssh_key_file": "C:\\keys\\old.ppk", "server_ssh_key_file": "C:\\keys\\gpu.ppk"});
        assert!(merge_public_settings(stored, json!({"S3_REGION": "kr-standard"}).as_object().unwrap()).is_ok());
    }
    #[test]
    fn a_stored_pasted_key_neither_blocks_other_saves_nor_survives_them() {
        let existing = json!({"pod_id": "assigned", "ssh_key_file": format!("{}\nb3Blbg==", fake_key_header("OPENSSH ")),
            "server_ssh_key_file": "PuTTY-User-Key-File-3: ssh-ed25519"});
        let saved = merge_public_settings(existing.clone(), json!({"S3_REGION": "kr-standard"}).as_object().unwrap())
            .expect("an unrelated save was blocked by a stored key-file value");
        assert_eq!(saved.get("S3_REGION"), Some(&json!("kr-standard")));
        assert_eq!(saved.get("pod_id"), Some(&json!("assigned")));
        assert!(!saved.contains_key("ssh_key_file") && !saved.contains_key("server_ssh_key_file"), "pasted key content was kept");
        assert!(merge_public_settings(existing.clone(), json!({"ssh_key_file": fake_key_header("RSA ")}).as_object().unwrap()).is_err());
        let replaced = merge_public_settings(existing, json!({"ssh_key_file": "C:\\keys\\id_ed25519"}).as_object().unwrap()).unwrap();
        assert_eq!(replaced.get("ssh_key_file"), Some(&json!("C:\\keys\\id_ed25519")));
    }
    #[test]
    fn server_sidecar_actions_are_named_operations() {
        for action in ["server_inspect", "server_facts", "runtime_reservations", "runtime_release"] {
            assert!(allowed(action), "{action} must be allowed");
        }
        for action in ["shell", "server_start", "server_stop", "ssh", "runtime_release_all"] { assert!(!allowed(action)); }
    }
    // Copied from allowed(): the legacy channel keeps exactly these 34 names.
    const LEGACY_ACTIONS: [&str; 34] = ["catalog", "save_profile", "dataset", "plan", "submit", "execute", "jobs", "logs",
        "cancel", "recover", "reconcile", "resume", "connection", "pod", "runtime_check", "runtime_prepare", "live_logs",
        "history", "compare", "model_revision", "dataset_preview", "job_progress", "capabilities", "s3_list", "s3_head",
        "s3_import", "model_search", "model_inspect", "model_import", "server_inspect", "server_facts",
        "runtime_reservations", "runtime_release", "tutorial_sample"];
    #[test]
    fn workflow_actions_are_a_closed_list_disjoint_from_legacy_actions() {
        let unique: std::collections::BTreeSet<&str> = WORKFLOW_ACTIONS.into_iter().collect();
        assert_eq!(unique.len(), 33);
        for action in WORKFLOW_ACTIONS {
            assert!(workflow_allowed(action), "{action}");
            assert!(!allowed(action), "the legacy channel accepts the workflow action {action}");
        }
        for action in LEGACY_ACTIONS {
            assert!(allowed(action), "the legacy action {action} is no longer allowed");
            assert!(!workflow_allowed(action), "the workflow channel accepts the legacy action {action}");
        }
        for action in ["", "shell", "controller_start", "executor_profile_save", "factory_action", "PROJECTS_LIST",
            "projects_list ", "graph_plan\n"] {
            assert!(!workflow_allowed(action) && !allowed(action), "{action:?}");
        }
        assert!(PROFILE_SCOPED_ACTIONS.iter().chain(HF_SCOPED_ACTIONS.iter()).all(|action| WORKFLOW_ACTIONS.contains(action)));
    }
    fn keys_named(value: &Value, names: &[&str]) -> Vec<String> {
        match value {
            Value::Object(map) => map.iter().flat_map(|(key, item)| {
                let mut found = keys_named(item, names);
                if names.contains(&key.as_str()) { found.push(key.clone()); }
                found
            }).collect(),
            Value::Array(items) => items.iter().flat_map(|item| keys_named(item, names)).collect(),
            _ => Vec::new(),
        }
    }
    #[test]
    fn workflow_requests_carry_public_settings_and_scoped_secrets_only() {
        use profiles::testing::{fake_key_header, synthetic, MemoryVault, TempRoot};
        let app = TempRoot::new("workflow-request");
        fs::write(app.path().join("settings.json"), json!({"MLFLOW_TRACKING_URI": "https://mlflow.invalid",
            "auth_mode": "token", "pod_id": "legacy-pod", "S3_REGION": "kr", "unknown": "x", "FACTORY_OPERATOR": 12,
            "ssh_key_file": fake_key_header("OPENSSH "), "RUNPOD_API_KEY": "legacy-plaintext-value"}).to_string()).unwrap();
        let mut secrets: Vec<String> = SECRETS.iter().map(|name| synthetic(&name.to_ascii_lowercase())).collect();
        let entries: Vec<(&str, &str)> = SECRETS.iter().copied().zip(secrets.iter().map(String::as_str)).collect();
        let vault = MemoryVault::with(&entries);
        let (key_a, key_b) = (synthetic("profile-a"), synthetic("profile-b"));
        let runpod = |name: &str, key: &str| json!({"provider": "existing_runpod", "name": name, "pod_id": "pod",
            "RUNPOD_API_KEY": key}).as_object().unwrap().clone();
        profiles::save_profile(app.path(), &vault, "gpu-a", &runpod("A", &key_a), None).unwrap();
        profiles::save_profile(app.path(), &vault, "gpu-b", &runpod("B", &key_b), None).unwrap();
        profiles::save_profile(app.path(), &vault, "lab", json!({"provider": "ssh_server", "name": "Lab"}).as_object().unwrap(), None).unwrap();
        // A stale RunPod entry under the SSH profile's ID (an earlier profile of that name) is never sent.
        let stale = synthetic("stale-lab");
        vault.set(&profiles::profile_secret_entry("lab", "RUNPOD_API_KEY"), &stale).unwrap();
        secrets.extend([key_a.clone(), key_b.clone(), "legacy-plaintext-value".into(), stale]);
        let scoped = |action: &str| PROFILE_SCOPED_ACTIONS.contains(&action) || HF_SCOPED_ACTIONS.contains(&action);
        let general = |action: &&str| !scoped(action) && !VAULT_FREE_ACTIONS.contains(action);
        for action in WORKFLOW_ACTIONS.into_iter().filter(general) {
            let request = build_workflow_request(app.path(), &vault, action, json!({"profile_id": "gpu-a"})).unwrap();
            let text = request.to_string();
            for secret in &secrets { assert!(!text.contains(secret.as_str()), "{action} carried a credential"); }
            assert!(keys_named(&request, &SECRETS).is_empty(), "{action}: {:?}", keys_named(&request, &SECRETS));
            assert!(!text.contains("PRIVATE KEY"), "{action} carried stored key content");
            let keys: std::collections::BTreeSet<&str> = request.as_object().unwrap().keys().map(String::as_str).collect();
            assert_eq!(keys, ["action", "channel", "data", "profiles", "root", "secrets", "settings"].into_iter().collect());
            assert_eq!(request["channel"], "workflow");
            assert_eq!(request["root"], json!(app.path().join("state")));
            assert_eq!(request["action"], action);
            assert_eq!(request["data"], json!({"profile_id": "gpu-a"}));
            assert_eq!(request["secrets"], json!({}));
            let settings = request["settings"].as_object().unwrap();
            for name in SECRETS { assert_eq!(settings.get(&format!("{name}_saved")), Some(&json!(true)), "{name}"); }
            for name in ["MLFLOW_TRACKING_URI", "auth_mode", "pod_id", "S3_REGION"] { assert!(settings.contains_key(name), "{name}"); }
            for name in ["unknown", "FACTORY_OPERATOR", "ssh_key_file"] { assert!(!settings.contains_key(name), "{name} was sent"); }
            assert!(settings.values().all(|value| value.is_string() || value == &json!(true)));
            let profiles = request["profiles"].as_array().unwrap();
            assert_eq!(profiles.len(), 3);
            assert!(profiles.iter().all(|profile| profile.get("secrets").is_none()));
            assert_eq!(profiles[0]["RUNPOD_API_KEY_saved"], true);
        }
        // The profile-scoped action carries exactly that profile's key, once.
        let request = build_workflow_request(app.path(), &vault, "executor_profile_inspect", json!({"profile_id": "gpu-b"})).unwrap();
        assert_eq!(request["secrets"], json!({"RUNPOD_API_KEY": key_b}));
        let text = request.to_string();
        assert_eq!(text.matches(key_b.as_str()).count(), 1);
        for secret in secrets.iter().filter(|secret| **secret != key_b) { assert!(!text.contains(secret.as_str())); }
        // An SSH, unknown or invalid profile ID, or another spelling of a stored ID, gets no key.
        for data in [json!({"profile_id": "lab"}), json!({"profile_id": "missing"}), json!({"profile_id": "../gpu-a"}),
            json!({"profile_id": "GPU-A"}), json!({"profile_id": "gpu-a."}), json!({"profile_id": 3}), json!({})] {
            let request = build_workflow_request(app.path(), &vault, "executor_profile_inspect", data.clone()).unwrap();
            assert_eq!(request["secrets"], json!({}), "{data}");
        }
        // The HF actions (the snapshot and 9.7's header-only target read) get only the legacy HF token.
        let hf = synthetic("hf_token");
        for action in ["model_root_snapshot", "model_root_targets"] {
            let request = build_workflow_request(app.path(), &vault, action, json!({"repo_id": "org/model"})).unwrap();
            assert_eq!(request["secrets"], json!({"HF_TOKEN": hf}), "{action}");
            let text = request.to_string();
            for secret in secrets.iter().filter(|secret| **secret != hf) { assert!(!text.contains(secret.as_str()), "{action}"); }
        }
        // Without a saved HF token they get no secret at all.
        let values: Vec<String> = SECRETS.iter().map(|name| synthetic(&name.to_ascii_lowercase())).collect();
        let without_hf: Vec<(&str, &str)> = SECRETS.iter().copied().zip(values.iter().map(String::as_str))
            .filter(|(name, _)| *name != "HF_TOKEN").collect();
        let no_token = MemoryVault::with(&without_hf);
        for action in ["model_root_snapshot", "model_root_targets"] {
            let request = build_workflow_request(app.path(), &no_token, action, json!({"repo_id": "org/model"})).unwrap();
            assert_eq!(request["secrets"], json!({}), "{action}");
        }
        // Only a JSON object is a workflow request body.
        for data in [json!([1]), json!("x"), json!(null)] {
            assert!(build_workflow_request(app.path(), &vault, "projects_list", data).is_err());
        }
    }
    #[test]
    fn settings_read_through_the_vault_keep_the_legacy_rules() {
        use profiles::testing::{synthetic, FailingVault, MemoryVault, TempRoot};
        let app = TempRoot::new("settings-vault");
        fs::write(app.path().join("settings.json"), json!({"auth_mode": "none", "MLFLOW_TRACKING_URI": "http://127.0.0.1:5000",
            "MLFLOW_OIDC_TOKEN_URL": "https://sso.invalid/token", "HF_TOKEN": "plaintext-in-file"}).to_string()).unwrap();
        let values: Vec<String> = SECRETS.iter().map(|name| synthetic(name)).collect();
        let entries: Vec<(&str, &str)> = SECRETS.iter().copied().zip(values.iter().map(String::as_str)).collect();
        let vault = MemoryVault::with(&entries);
        let public = read_settings_from(app.path(), &vault, false).unwrap();
        for value in &values { assert!(!public.to_string().contains(value.as_str())); }
        assert!(!public.to_string().contains("plaintext-in-file"));
        for name in SECRETS { assert_eq!(public[format!("{name}_saved")], true); }
        assert_eq!(public["MLFLOW_OIDC_TOKEN_URL"], "https://sso.invalid/token", "the public view keeps every MLflow field");
        // The sidecar view of auth_mode "none" holds no MLflow credential; the other credentials are unchanged.
        let sidecar = read_settings_from(app.path(), &vault, true).unwrap();
        for name in MLFLOW_CREDENTIALS { assert!(sidecar.get(name).is_none(), "none kept {name}"); }
        assert_eq!(sidecar["S3_SECRET_ACCESS_KEY"], synthetic("S3_SECRET_ACCESS_KEY"));
        assert_eq!(sidecar["RUNPOD_API_KEY"], synthetic("RUNPOD_API_KEY"));
        let empty = TempRoot::new("settings-empty");
        assert_eq!(read_settings_from(empty.path(), &MemoryVault::default(), true).unwrap(), json!({}));
        fs::write(empty.path().join("settings.json"), "[]").unwrap();
        assert!(read_settings_from(empty.path(), &MemoryVault::default(), false).is_err());
        assert!(read_settings_from(app.path(), &FailingVault, false).is_err());
    }
    #[test]
    fn legacy_requests_keep_exactly_their_bytes() {
        let settings = json!({"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "RUNPOD_API_KEY_saved": true});
        let request = legacy_request(Path::new("C:\\app"), settings, "jobs", json!({"limit": 2}));
        // `json!({"root", "settings", "action", "data"})` as factory_action always built it; serde_json is built
        // without preserve_order (Cargo.lock), so the wire order is sorted, exactly as before.
        let keys: Vec<&str> = request.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys, ["action", "data", "root", "settings"]);
        assert_eq!(request.to_string(), r#"{"action":"jobs","data":{"limit":2},"root":"C:\\app\\state","settings":{"MLFLOW_TRACKING_URI":"https://mlflow.invalid","RUNPOD_API_KEY_saved":true}}"#);
        // factory_action sends exactly this envelope and nothing else.
        let source = include_str!("main.rs");
        let action = &source[source.find("async fn factory_action(").unwrap()..];
        let action = &action[..action.find("\n}\n").unwrap()];
        assert!(action.contains("run_sidecar(&legacy_request(&root, settings, &action, data))"), "{action}");
    }
    #[test]
    fn controller_stop_and_status_need_no_vault_and_survive_broken_files() {
        use profiles::testing::{synthetic, FailingVault, MemoryVault, TempRoot};
        let app = TempRoot::new("lifecycle");
        fs::write(app.path().join("settings.json"), json!({"MLFLOW_TRACKING_URI": "https://mlflow.invalid",
            "auth_mode": "token", "pod_id": "legacy-pod"}).to_string()).unwrap();
        let values: Vec<String> = SECRETS.iter().map(|name| synthetic(name)).collect();
        let entries: Vec<(&str, &str)> = SECRETS.iter().copied().zip(values.iter().map(String::as_str)).collect();
        let vault = MemoryVault::with(&entries);
        let key = synthetic("profile-a");
        let runpod = json!({"provider": "existing_runpod", "name": "A", "pod_id": "pod", "RUNPOD_API_KEY": key});
        profiles::save_profile(app.path(), &vault, "gpu-a", runpod.as_object().unwrap(), None).unwrap();
        profiles::save_profile(app.path(), &vault, "lab", json!({"provider": "ssh_server", "name": "Lab"}).as_object().unwrap(), None).unwrap();
        vault.clear_reads();
        let status = build_workflow_request(app.path(), &vault, "controller_status", json!({})).unwrap();
        let stop = build_workflow_request(app.path(), &vault, "controller_stop", json!({})).unwrap();
        assert!(vault.read_names().is_empty(), "lifecycle requests read {:?}", vault.read_names());
        // Status carries the public files without credential flags (no Credential Manager access per poll).
        assert_eq!(status["settings"], json!({"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "token",
            "pod_id": "legacy-pod"}));
        assert_eq!(status["profiles"], json!([
            {"profile_id": "gpu-a", "fields": {"provider": "existing_runpod", "name": "A", "pod_id": "pod"}},
            {"profile_id": "lab", "fields": {"provider": "ssh_server", "name": "Lab"}}]));
        assert_eq!((&status["secrets"], &status["action"]), (&json!({}), &json!("controller_status")));
        assert_eq!((&stop["settings"], &stop["profiles"], &stop["secrets"]), (&json!({}), &json!([]), &json!({})));
        for request in [&status, &stop] {
            for value in values.iter().chain([&key]) { assert!(!request.to_string().contains(value.as_str())); }
        }
        // Unreadable settings and profile files and an unavailable Credential Manager block neither.
        fs::write(app.path().join("settings.json"), "{broken").unwrap();
        fs::write(app.path().join(profiles::PROFILES_FILE), "not json").unwrap();
        for action in VAULT_FREE_ACTIONS {
            let request = build_workflow_request(app.path(), &FailingVault, action, json!({})).unwrap();
            assert_eq!((&request["settings"], &request["profiles"]), (&json!({}), &json!([])), "{action}");
        }
        // Every other action still needs readable settings and profiles.
        assert!(build_workflow_request(app.path(), &vault, "projects_list", json!({})).is_err());
    }
    #[test]
    fn the_one_request_sidecar_keeps_no_arguments_and_no_environment() {
        let command = request_command(Path::new("C:\\Program Files\\LLM Tuning Factory\\factory-bridge.exe"));
        assert_eq!(command.get_args().count(), 0, "the legacy sidecar mode has no argv");
        assert_eq!(command.get_envs().count(), 0, "credentials are only sent through stdin");
        assert!(command.get_current_dir().is_none());
    }
}
