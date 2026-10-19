#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Map, Value};
use std::{fs, io::Write, path::PathBuf, process::{Command, Stdio}, sync::Mutex};
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
struct SaveLock(Mutex<()>);

fn app_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app.path().app_data_dir().map_err(|_| "앱 저장 폴더를 찾을 수 없습니다.")?;
    fs::create_dir_all(&root).map_err(|_| "앱 저장 폴더를 만들 수 없습니다.")?;
    Ok(root)
}

fn entry(name: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new("kr.kbiofoundry.tuningfactory", name).map_err(|_| "Windows 자격 증명 저장소 오류".into())
}

fn read_settings(root: &PathBuf, secrets: bool) -> Result<Value, String> {
    let path = root.join("settings.json");
    let mut value = if path.exists() {
        serde_json::from_slice::<Value>(&fs::read(path).map_err(|_| "설정을 읽을 수 없습니다.")?)
            .map_err(|_| "설정 파일이 손상되었습니다.")?
    } else { json!({}) };
    if !value.is_object() { return Err("설정 파일이 손상되었습니다.".into()); }
    // Never return legacy or hand-edited plaintext credentials from the public settings file.
    for name in SECRETS { value.as_object_mut().unwrap().remove(name); }
    for name in SECRETS {
        match entry(name)?.get_password() {
            Ok(secret) => {
                value[format!("{name}_saved")] = json!(true);
                if secrets { value[name] = json!(secret); }
            },
            Err(keyring::Error::NoEntry) => {},
            Err(_) => return Err("Windows 자격 증명에 접근할 수 없습니다.".into()),
        }
    }
    filter_mlflow_auth(value.as_object_mut().unwrap(), secrets);
    Ok(value)
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

/// Pasted OpenSSH/PEM or PuTTY (.ppk) key content in a key-file path field.
fn pasted_key(name: &str, value: &Value) -> bool {
    ["ssh_key_file", "server_ssh_key_file"].contains(&name) && value.as_str().is_some_and(|text|
        ["PRIVATE KEY", "PuTTY-User-Key-File-", "Private-Lines:"].iter().any(|marker| text.contains(marker)))
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
        return Err("SSH 개인키 파일에는 키 내용이 아니라 키 파일 경로를 입력하세요.".into());
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
    let selection = text("server_selected_gpu_uuids");
    let valid_selection = selection.is_empty() || matches!(serde_json::from_str::<Value>(selection),
        Ok(Value::Array(items)) if items.len() <= 8 && items.iter().all(Value::is_string));
    if !valid_selection {
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
fn forget_credentials() -> Result<(), String> {
    for name in SECRETS {
        match entry(name)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => {},
            Err(_) => return Err("인증값 삭제 실패".into()),
        }
    }
    Ok(())
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
        "server_inspect" | "server_facts" | "runtime_reservations" | "runtime_release")
}

#[tauri::command]
async fn factory_action(app: tauri::AppHandle, action: String, data: Value) -> Result<Value, String> {
    if !allowed(&action) { return Err("허용하지 않은 작업".into()); }
    let root = app_root(&app)?;
    let settings = read_settings(&root, true)?;
    tauri::async_runtime::spawn_blocking(move || {
        let binary = if cfg!(debug_assertions) {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/factory-bridge-x86_64-pc-windows-msvc.exe")
        } else {
            std::env::current_exe().map_err(|_| "앱 경로 오류")?.parent().ok_or("앱 경로 오류")?.join("factory-bridge.exe")
        };
        let mut command = Command::new(binary);
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW; credentials are only sent through stdin.
        }
        let mut child = command.spawn().map_err(|_| "학습 엔진을 실행할 수 없습니다. 설치 파일을 확인해 주세요.")?;
        let request = json!({"root": root.join("state"), "settings": settings, "action": action, "data": data});
        {
            let mut stdin = child.stdin.take().ok_or("엔진 입력 연결 오류")?;
            writeln!(stdin, "{}", request).map_err(|_| "엔진 요청 전송 실패")?;
        }
        let output = child.wait_with_output().map_err(|_| "엔진 응답 오류")?;
        let response: Value = serde_json::from_slice(&output.stdout)
            .map_err(|_| "엔진 응답을 해석할 수 없습니다.")?;
        if response["ok"] == true { Ok(response["result"].clone()) }
        else { Err(response["error"].as_str().unwrap_or("실행 오류").to_owned()) }
    }).await.map_err(|_| "엔진 작업 오류")?
}

fn main() {
    tauri::Builder::default()
        .manage(SaveLock(Mutex::new(())))
        .invoke_handler(tauri::generate_handler![settings_get, settings_save, forget_credentials, pick_file, factory_action])
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
    #[test]
    fn key_file_settings_refuse_pasted_private_keys() {
        for name in ["ssh_key_file", "server_ssh_key_file"] {
            for pasted in ["-----BEGIN OPENSSH PRIVATE KEY----\nb3BlbnNzaC1rZXktdjE=", "-----BEGIN RSA PRIVATE KEY----",
                "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none", "Public-Lines: 2\nAAAA\nPrivate-Lines: 1\nAAAA"] {
                let error = merge(json!({name: pasted})).expect_err("a pasted private key was accepted");
                assert!(error.contains("파일 경로"), "{error}");
            }
            assert!(merge(json!({name: "C:\\Users\\researcher\\.ssh\\id_ed25519"})).is_ok());
            assert!(merge(json!({name: "C:\\Users\\researcher\\keys\\server.ppk"})).is_ok());
        }
    }
    #[test]
    fn a_stored_pasted_key_neither_blocks_other_saves_nor_survives_them() {
        let existing = json!({"pod_id": "assigned", "ssh_key_file": "-----BEGIN OPENSSH PRIVATE KEY----\nb3Blbg==",
            "server_ssh_key_file": "PuTTY-User-Key-File-3: ssh-ed25519"});
        let saved = merge_public_settings(existing.clone(), json!({"S3_REGION": "kr-standard"}).as_object().unwrap())
            .expect("an unrelated save was blocked by a stored key-file value");
        assert_eq!(saved.get("S3_REGION"), Some(&json!("kr-standard")));
        assert_eq!(saved.get("pod_id"), Some(&json!("assigned")));
        assert!(!saved.contains_key("ssh_key_file") && !saved.contains_key("server_ssh_key_file"), "pasted key content was kept");
        assert!(merge_public_settings(existing.clone(), json!({"ssh_key_file": "-----BEGIN RSA PRIVATE KEY----"}).as_object().unwrap()).is_err());
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
}
