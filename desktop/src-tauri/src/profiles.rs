//! Executor profiles (Task 9 plan D5, D6): public fields in `<app_root>/executor-profiles.json`, one RunPod API key
//! per RunPod profile in Windows Credential Manager (`executor-profile/<id>/RUNPOD_API_KEY`). Every credential access
//! goes through the `Vault` trait, so tests use an in-memory vault and never touch the real Credential Manager.

use serde_json::{json, Map, Value};
use std::{collections::BTreeSet, fs, path::Path, sync::Mutex};

pub const SERVICE: &str = "kr.kbiofoundry.tuningfactory";
pub const PROFILE_SECRETS: [&str; 1] = ["RUNPOD_API_KEY"];
pub const RUNPOD_PROFILE_FIELDS: [&str; 5] = ["provider", "name", "pod_id", "ssh_fingerprint", "ssh_key_file"];
pub const SERVER_PROFILE_FIELDS: [&str; 10] = ["provider", "name", "server_ssh_host", "server_ssh_port",
    "server_ssh_username", "server_ssh_fingerprint", "server_remote_root", "server_ssh_key_file",
    "server_total_hourly_cost_usd", "server_selected_gpu_uuids"];
pub const MAX_PROFILES: usize = 32;
pub const MAX_PROFILE_ID: usize = 64;
pub const PROFILES_FILE: &str = "executor-profiles.json";
/// The sidecar check of D5 (9.3): `{"profile_id"}` in, `conflict`/`profile_in_use` or `ok` with `affected_runs` out.
pub const EDIT_CHECK_ACTION: &str = "executor_profile_edit_check";

const RUNPOD: &str = "existing_runpod";
const SERVER: &str = "ssh_server";
const LEGACY_RUNPOD_KEY: &str = "RUNPOD_API_KEY";      // the legacy connection's vault entry
const MAX_VALUE: usize = 2048;
const MAX_NAME: usize = 200;
const INVALID_ID: &str = "실행 프로필 ID는 영문·숫자·_·-·. 으로 된 64자 이내여야 하며, .으로 시작하거나 estimate-로 시작하거나 장치 이름(CON, NUL 등)일 수 없습니다.";
const ID_TAKEN: &str = "대소문자나 끝의 마침표만 다른 실행 프로필 ID가 이미 있습니다. 다른 ID를 쓰세요.";
const ID_NOT_EXACT: &str = "저장된 실행 프로필 ID와 대소문자·끝의 마침표까지 정확히 같은 ID로만 삭제할 수 있습니다.";
const CORRUPT: &str = "실행 프로필 파일이 손상되었습니다.";
const READ_FAILED: &str = "실행 프로필을 읽을 수 없습니다.";
const SAVE_FAILED: &str = "실행 프로필 저장 실패";
const UNKNOWN_FIELD: &str = "허용하지 않은 실행 프로필 항목";
const NOT_STRING: &str = "실행 프로필 값은 문자열이어야 합니다.";
const TOO_LONG: &str = "실행 프로필 값이 너무 깁니다.";
const PROVIDER: &str = "실행 프로필의 연결 대상은 RunPod 또는 GPU 서버여야 합니다.";
const PROVIDER_FIXED: &str = "저장된 실행 프로필의 연결 대상은 바꿀 수 없습니다. 새 실행 프로필을 만드세요.";
const NAME_REQUIRED: &str = "실행 프로필 이름을 200자 이내로 입력하세요.";
const KEY_PATH_RELATIVE: &str = "SSH 키 파일은 이 PC의 전체 경로(예: C:\\Users\\이름\\.ssh\\id_ed25519)로 입력하세요.";
const KEY_CONTENT: &str ="실행 프로필에는 개인키 내용을 입력할 수 없습니다. 키 파일 경로만 입력하세요.";
const GPU_SELECTION: &str = "선택한 GPU 목록은 중복 없는 GPU UUID 문자열 최대 8개의 JSON 배열이어야 합니다.";
const COST: &str = "시간당 비용은 0 이상의 숫자(예: 0, 2.75)로 입력하세요.";
const PORT: &str = "SSH 포트는 1~65535 사이의 숫자여야 합니다.";
const TOO_MANY: &str = "실행 프로필은 최대 32개까지 저장할 수 있습니다.";
const SECRET_PROVIDER: &str = "RunPod API 키는 RunPod 실행 프로필에만 저장할 수 있습니다.";
const LEGACY_MISSING: &str = "복사할 기존 RunPod API 키가 저장되어 있지 않습니다.";
const LEGACY_AND_INPUT: &str = "RunPod API 키를 입력하면서 기존 키 복사를 함께 선택할 수 없습니다.";
const LOCK_FAILED: &str = "설정 잠금 오류";
const EDIT_CHECK_FAILED: &str = "실행 프로필 사용 여부를 확인할 수 없어 변경하지 않았습니다.";
const PROFILE_IN_USE: &str = "이 실행 프로필로 실행 중이거나 결과 확인이 끝나지 않은 작업이 있어 수정·삭제할 수 없습니다.";

pub trait Vault {
    fn get(&self, name: &str) -> Result<Option<String>, String>;
    fn set(&self, name: &str, value: &str) -> Result<(), String>;
    fn delete(&self, name: &str) -> Result<(), String>;      // Ok for a missing entry
}

/// Windows Credential Manager under `SERVICE`, with the error texts the settings commands always used.
pub struct KeyringVault;

impl Vault for KeyringVault {
    fn get(&self, name: &str) -> Result<Option<String>, String> {
        match crate::entry(name)?.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err("Windows 자격 증명에 접근할 수 없습니다.".into()),
        }
    }
    fn set(&self, name: &str, value: &str) -> Result<(), String> {
        crate::entry(name)?.set_password(value).map_err(|_| "인증값 보안 저장 실패".into())
    }
    fn delete(&self, name: &str) -> Result<(), String> {
        match crate::entry(name)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err("인증값 삭제 실패".into()),
        }
    }
}

/// `^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$` (9.1's `NativeProfile.profile_id`), not a planning estimate ID and not a
/// DOS device name (the ID also names files under the state directory).
pub fn valid_profile_id(id: &str) -> bool {
    let bytes = id.as_bytes();
    let allowed = |byte: &u8| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.');
    !bytes.is_empty() && bytes.len() <= MAX_PROFILE_ID && bytes[0] != b'.' && bytes.iter().all(allowed)
        && !id.to_ascii_lowercase().starts_with("estimate-") && !device_name(id)
}

fn device_name(id: &str) -> bool {
    let stem = id.split('.').next().unwrap_or("").to_ascii_lowercase();
    matches!(stem.as_str(), "con" | "prn" | "aux" | "nul")
        || (stem.len() == 4 && (stem.starts_with("com") || stem.starts_with("lpt")) && stem.as_bytes()[3].is_ascii_digit())
}

pub fn profile_secret_entry(id: &str, name: &str) -> String { format!("executor-profile/{id}/{name}") }

/// The identity Windows gives a profile ID: file names and Credential Manager targets ignore case, file names drop
/// trailing dots. Equal to 9.1's `casefold().rstrip(".")` on valid (ASCII) IDs.
pub fn profile_id_key(id: &str) -> String { id.trim_end_matches('.').to_ascii_lowercase() }

fn provider_fields(provider: &str) -> Option<&'static [&'static str]> {
    match provider {
        RUNPOD => Some(&RUNPOD_PROFILE_FIELDS),
        SERVER => Some(&SERVER_PROFILE_FIELDS),
        _ => None,
    }
}

/// Validates submitted public fields against the stored profile and returns the merged fields. The provider is fixed
/// once saved; only that provider's fields are accepted; values are strings of at most 2048 bytes; key content is
/// refused in every field and .ppk files in key-file fields. Credentials never pass through here.
pub fn merge_profile(existing: Option<&Map<String, Value>>, input: &Map<String, Value>)
    -> Result<Map<String, Value>, String> {
    for value in input.values() {
        let text = value.as_str().ok_or(NOT_STRING)?;
        if text.len() > MAX_VALUE { return Err(TOO_LONG.into()); }
    }
    let stored = existing.and_then(|fields| fields.get("provider")).and_then(Value::as_str);
    let provider = match (stored, input.get("provider").and_then(Value::as_str)) {
        (Some(stored), Some(submitted)) if stored != submitted => return Err(PROVIDER_FIXED.into()),
        (Some(stored), _) => stored,
        (None, Some(submitted)) => submitted,
        (None, None) => return Err(PROVIDER.into()),
    };
    let allowed = provider_fields(provider).ok_or(PROVIDER)?;
    if input.keys().any(|name| !allowed.contains(&name.as_str())) { return Err(UNKNOWN_FIELD.into()); }
    if input.iter().any(|(name, value)| crate::pasted_key(name, value)) { return Err(crate::PASTED_KEY_REFUSED.into()); }
    // executor-profiles.json is plaintext: key content belongs in no field, not only in the key-file paths.
    if input.values().any(|value| value.as_str().is_some_and(crate::key_content)) { return Err(KEY_CONTENT.into()); }
    if input.iter().any(|(name, value)| crate::ppk_path(name, value)) { return Err(crate::PPK_REFUSED.into()); }
    let mut merged = existing.map(|fields| public_fields(fields, allowed)).unwrap_or_default();
    for (name, value) in input { merged.insert(name.clone(), value.clone()); }
    merged.insert("provider".into(), json!(provider));
    validate_fields(&merged)?;
    Ok(merged)
}

fn public_fields(fields: &Map<String, Value>, allowed: &[&str]) -> Map<String, Value> {
    fields.iter()
        .filter(|(name, value)| allowed.contains(&name.as_str())
            && value.as_str().is_some_and(|text| text.len() <= MAX_VALUE && !crate::key_content(text)))
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect()
}

fn validate_fields(fields: &Map<String, Value>) -> Result<(), String> {
    let text = |name: &str| fields.get(name).and_then(Value::as_str).unwrap_or("");
    let name = text("name");
    if name.trim().is_empty() || name.chars().count() > MAX_NAME { return Err(NAME_REQUIRED.into()); }
    let selection = text("server_selected_gpu_uuids");
    let unique = match serde_json::from_str::<Value>(selection) {
        Ok(Value::Array(items)) => items.iter().filter_map(Value::as_str).collect::<BTreeSet<_>>().len() == items.len(),
        _ => selection.is_empty(),
    };
    if !crate::valid_gpu_selection(selection) || !unique { return Err(GPU_SELECTION.into()); }
    let cost = text("server_total_hourly_cost_usd");
    if !cost.is_empty() && !plain_decimal(cost) { return Err(COST.into()); }
    let port = text("server_ssh_port");
    let valid_port = port.len() <= 5 && port.bytes().all(|byte| byte.is_ascii_digit())
        && port.parse::<u32>().is_ok_and(|number| (1..=65535).contains(&number));
    if !port.is_empty() && !valid_port { return Err(PORT.into()); }
    // The D11 transport hash resolves the key path, so a relative one would depend on each process's working
    // directory (planning sidecar versus controller). UNC and device paths (`\\server\share`, `\\?\`, `\\.\`,
    // any two leading separators) are network or device access (D12). Profiles only; the legacy connection settings
    // are unchanged.
    let local_absolute = |path: &str| Path::new(path).is_absolute()
        && !(path.len() >= 2 && path.as_bytes()[..2].iter().all(|byte| matches!(byte, b'\\' | b'/')));
    if crate::KEY_FILE_FIELDS.iter().any(|name| !text(name).is_empty() && !local_absolute(text(name))) {
        return Err(KEY_PATH_RELATIVE.into());
    }
    Ok(())
}

/// A non-negative decimal such as `0`, `12` or `2.75` (no sign, exponent, NaN or infinity).
fn plain_decimal(text: &str) -> bool {
    let (whole, fraction) = text.split_once('.').unwrap_or((text, ""));
    let digits = |part: &str| part.len() <= 12 && part.bytes().all(|byte| byte.is_ascii_digit());
    !whole.is_empty() && digits(whole) && digits(fraction) && (fraction.is_empty() != text.contains('.'))
}

/// The raw store: `{"schema_version": 1, "profiles": {id: {public fields}}}`; a missing file is an empty store.
fn read_store(root: &Path) -> Result<Map<String, Value>, String> {
    let path = root.join(PROFILES_FILE);
    if !path.exists() { return Ok(Map::new()); }
    let bytes = fs::read(&path).map_err(|_| READ_FAILED)?;
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(mut top)) if top.get("schema_version") == Some(&json!(1)) => match top.remove("profiles") {
            Some(Value::Object(profiles)) => Ok(profiles),
            _ => Err(CORRUPT.into()),
        },
        _ => Err(CORRUPT.into()),
    }
}

/// The profiles the app serves, sorted by ID: valid IDs and providers, allowlisted string fields without key content,
/// and only the first of IDs that Windows would treat as the same name (`profile_id_key`).
fn served(store: &Map<String, Value>) -> Vec<(String, Map<String, Value>)> {
    let mut ids: Vec<&String> = store.keys().filter(|id| valid_profile_id(id)).collect();
    ids.sort();
    let mut seen = BTreeSet::new();
    ids.into_iter().filter_map(|id| {
        let fields = store[id].as_object()?;
        let allowed = provider_fields(fields.get("provider")?.as_str()?)?;
        seen.insert(profile_id_key(id)).then(|| (id.clone(), public_fields(fields, allowed)))
    }).collect()
}

/// A copy of a stored value without any string holding key content (entries the app does not serve).
fn without_key_content(value: &Value) -> Option<Value> {
    match value {
        Value::String(text) if crate::key_content(text) => None,
        Value::Object(map) => Some(Value::Object(map.iter()
            .filter_map(|(name, item)| Some((name.clone(), without_key_content(item)?))).collect())),
        Value::Array(items) => Some(Value::Array(items.iter().filter_map(without_key_content).collect())),
        other => Some(other.clone()),
    }
}

/// Writes served profiles sanitized and keeps every entry the app cannot serve (minus key content), so no save ever
/// drops an ID whose vault entry `forget_credentials` must still find. The file is written to a temporary file and
/// renamed over the old one: `std::fs::rename` replaces an existing file atomically on Windows
/// (`MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`) and on POSIX, so a reader or a crash sees the old or the new
/// store, never a partial one. Credentials stay exclusively in Credential Manager.
fn write_store(root: &Path, store: &Map<String, Value>) -> Result<(), String> {
    let served: Map<String, Value> = served(store).into_iter().map(|(id, fields)| (id, Value::Object(fields))).collect();
    let profiles: Map<String, Value> = store.iter().map(|(id, entry)| (id.clone(), served.get(id).cloned()
        .unwrap_or_else(|| without_key_content(entry).unwrap_or(Value::Null)))).collect();
    let body = serde_json::to_vec_pretty(&json!({"schema_version": 1, "profiles": profiles})).map_err(|_| SAVE_FAILED)?;
    let temporary = root.join("executor-profiles.tmp");
    fs::write(&temporary, body).map_err(|_| SAVE_FAILED)?;
    if fs::rename(&temporary, root.join(PROFILES_FILE)).is_err() {
        let _ = fs::remove_file(&temporary);
        return Err(SAVE_FAILED.into());
    }
    Ok(())
}

/// Another stored ID (served or not) that Windows treats as the same name, so it shares the vault entry.
fn colliding_id(store: &Map<String, Value>, id: &str) -> bool {
    store.keys().any(|stored| stored != id && valid_profile_id(stored) && profile_id_key(stored) == profile_id_key(id))
}

fn is_runpod(fields: &Map<String, Value>) -> bool { fields.get("provider").and_then(Value::as_str) == Some(RUNPOD) }

/// `{"profile_id", "fields", "RUNPOD_API_KEY_saved"}`, plus `"secrets"` (that profile's own key only) when asked.
fn view(id: &str, fields: &Map<String, Value>, vault: &dyn Vault, secrets: bool) -> Result<Value, String> {
    let key = if is_runpod(fields) { vault.get(&profile_secret_entry(id, PROFILE_SECRETS[0]))? } else { None };
    let mut view = json!({"profile_id": id, "fields": fields, "RUNPOD_API_KEY_saved": key.is_some()});
    if secrets {
        let mut scoped = Map::new();
        if let Some(key) = key { scoped.insert(PROFILE_SECRETS[0].into(), Value::String(key)); }
        view["secrets"] = Value::Object(scoped);
    }
    Ok(view)
}

/// `{"profiles": [...]}` sorted by ID. Secret values appear only with `secrets` (the broker), never in the public view.
pub fn read_profiles(root: &Path, vault: &dyn Vault, secrets: bool) -> Result<Value, String> {
    let profiles = served(&read_store(root)?);
    if profiles.len() > MAX_PROFILES { return Err(CORRUPT.into()); }
    let views = profiles.iter().map(|(id, fields)| view(id, fields, vault, secrets)).collect::<Result<Vec<_>, _>>()?;
    Ok(json!({"profiles": views}))
}

/// The served profiles' public fields only, without any vault access (`controller_status`).
pub fn public_profiles(root: &Path) -> Result<Vec<Value>, String> {
    let profiles = served(&read_store(root)?);
    if profiles.len() > MAX_PROFILES { return Err(CORRUPT.into()); }
    Ok(profiles.into_iter().map(|(id, fields)| json!({"profile_id": id, "fields": fields})).collect())
}

/// The RunPod key of exactly this stored RunPod profile, for `executor_profile_inspect`; None for anything else.
pub fn profile_secret(root: &Path, vault: &dyn Vault, id: &str) -> Result<Option<String>, String> {
    if !valid_profile_id(id) { return Ok(None); }
    if !served(&read_store(root)?).iter().any(|(stored, fields)| stored == id && is_runpod(fields)) { return Ok(None); }
    vault.get(&profile_secret_entry(id, PROFILE_SECRETS[0]))
}

struct PreparedSave { store: Map<String, Value>, fields: Map<String, Value>, secret: Option<String> }

/// Every check of a save, before any write: ID, collisions, count, fields and where a key may go.
fn prepare_save(root: &Path, id: &str, input: &Map<String, Value>, legacy_key: Option<String>)
    -> Result<PreparedSave, String> {
    if !valid_profile_id(id) { return Err(INVALID_ID.into()); }
    let store = read_store(root)?;
    let profiles = served(&store);
    let existing = profiles.iter().find(|(stored, _)| stored == id).map(|(_, fields)| fields);
    if existing.is_none() {
        if colliding_id(&store, id) { return Err(ID_TAKEN.into()); }
        if profiles.len() >= MAX_PROFILES { return Err(TOO_MANY.into()); }
    }
    let mut public = input.clone();
    let typed = match public.remove(PROFILE_SECRETS[0]) {
        None => None,
        Some(Value::String(text)) if text.is_empty() => None,      // empty keeps the saved key
        Some(Value::String(text)) if text.len() <= MAX_VALUE => Some(text),
        Some(Value::String(_)) => return Err(TOO_LONG.into()),
        Some(_) => return Err(NOT_STRING.into()),
    };
    let fields = merge_profile(existing, &public)?;
    if typed.is_some() && legacy_key.is_some() { return Err(LEGACY_AND_INPUT.into()); }
    let secret = typed.or(legacy_key);
    if secret.is_some() && !is_runpod(&fields) { return Err(SECRET_PROVIDER.into()); }
    Ok(PreparedSave { store, fields, secret })
}

/// Saves one profile: validates everything first, then writes a non-empty key to that profile's vault entry, then the
/// public file. If the file cannot be written, the vault entry is restored to what it was (removed for a new
/// profile), so no key is orphaned. Returns the public view.
pub fn save_profile(root: &Path, vault: &dyn Vault, id: &str, input: &Map<String, Value>,
                    legacy_key: Option<String>) -> Result<Value, String> {
    let PreparedSave { mut store, fields, secret } = prepare_save(root, id, input, legacy_key)?;
    let entry = profile_secret_entry(id, PROFILE_SECRETS[0]);
    let previous = match &secret {
        Some(secret) => {
            let previous = vault.get(&entry)?;
            vault.set(&entry, secret)?;
            Some(previous)
        }
        None => None,
    };
    store.insert(id.to_owned(), Value::Object(fields.clone()));
    if let Err(error) = write_store(root, &store) {
        match previous {
            Some(Some(old)) => { let _ = vault.set(&entry, &old); }
            Some(None) => { let _ = vault.delete(&entry); }
            None => {}
        }
        return Err(error);
    }
    view(id, &fields, vault, false)
}

fn prepare_delete(root: &Path, id: &str) -> Result<Map<String, Value>, String> {
    if !valid_profile_id(id) { return Err(INVALID_ID.into()); }
    let store = read_store(root)?;
    // Credential Manager would resolve another spelling of a stored ID (a shadowed hand-edited entry included) to that
    // profile's entry, so only the served profile itself may delete a shared name.
    let served_exactly = served(&store).iter().any(|(stored, _)| stored == id);
    if !served_exactly && colliding_id(&store, id) { return Err(ID_NOT_EXACT.into()); }
    Ok(store)
}

/// Deletes that profile's vault entry, then its public entry. A profile that does not exist is a no-op.
pub fn delete_profile(root: &Path, vault: &dyn Vault, id: &str) -> Result<(), String> {
    let mut store = prepare_delete(root, id)?;
    for name in PROFILE_SECRETS { vault.delete(&profile_secret_entry(id, name))?; }
    if store.remove(id).is_some() { write_store(root, &store)?; }
    Ok(())
}

/// Deletes the vault entries of every profile ID the file names (`forget_credentials`); the public profiles stay. The
/// IDs are read leniently (any `profiles` object, whatever the schema version), every entry is tried even after a
/// failure, and the first problem (an unexpected, unreadable or unparsable file, a failed delete) is returned at the
/// end. A missing file is nothing to forget.
pub fn forget_profile_secrets(root: &Path, vault: &dyn Vault) -> Result<(), String> {
    let path = root.join(PROFILES_FILE);
    if !path.exists() { return Ok(()); }
    let bytes = fs::read(&path).map_err(|_| READ_FAILED)?;
    let parsed: Value = serde_json::from_slice(&bytes).map_err(|_| CORRUPT)?;
    let profiles = parsed.get("profiles").and_then(Value::as_object).ok_or(CORRUPT)?;
    let mut result = if parsed.get("schema_version") == Some(&json!(1)) { Ok(()) } else { Err(CORRUPT.to_owned()) };
    for id in profiles.keys().filter(|id| valid_profile_id(id)) {
        for name in PROFILE_SECRETS {
            if let Err(error) = vault.delete(&profile_secret_entry(id, name)) { result = result.and(Err(error)); }
        }
    }
    result
}

/// The sidecar's `executor_profile_edit_check` answer: `ok` gives `affected_runs`; `conflict` (`profile_in_use`)
/// refuses with its message; anything else, including a missing check, refuses too (fail closed).
pub fn edit_check_outcome(answer: Result<Value, String>) -> Result<Value, String> {
    let answer = answer.map_err(|error| format!("{EDIT_CHECK_FAILED} ({error})"))?;
    let messages: Vec<&str> = answer.get("issues").and_then(Value::as_array).into_iter().flatten()
        .filter_map(|issue| issue.get("message").and_then(Value::as_str)).collect();
    match answer.get("status").and_then(Value::as_str) {
        Some("ok") => answer.pointer("/data/affected_runs").filter(|runs| runs.is_array()).cloned()
            .ok_or_else(|| EDIT_CHECK_FAILED.to_owned()),
        Some("conflict") if !messages.is_empty() => Err(messages.join(" ")),
        Some("conflict") => Err(PROFILE_IN_USE.into()),
        _ if messages.is_empty() => Err(EDIT_CHECK_FAILED.into()),
        _ => Err(format!("{EDIT_CHECK_FAILED} ({})", messages.join(" "))),
    }
}

/// `executor_profile_save` (D5): validate, ask the sidecar whether a job pinned to the profile holds a lock or is
/// active, then save under the settings lock. Returns `{"profile", "affected_runs"}`.
pub fn save_profile_checked(root: &Path, vault: &dyn Vault, id: &str, input: &Map<String, Value>,
                            copy_legacy: bool, check: &mut dyn FnMut(&str) -> Result<Value, String>,
                            lock: &Mutex<()>) -> Result<Value, String> {
    if !valid_profile_id(id) { return Err(INVALID_ID.into()); }
    let legacy_key = if copy_legacy { Some(vault.get(LEGACY_RUNPOD_KEY)?.ok_or(LEGACY_MISSING)?) } else { None };
    // A refused edit never costs a sidecar start, and the sidecar never sees an invalid ID.
    prepare_save(root, id, input, legacy_key.clone())?;
    let affected_runs = edit_check_outcome(check(id))?;
    let _guard = lock.lock().map_err(|_| LOCK_FAILED)?;
    let profile = save_profile(root, vault, id, input, legacy_key)?;
    Ok(json!({"profile": profile, "affected_runs": affected_runs}))
}

/// `executor_profile_delete` (D5): the same check, then the delete. Returns `{"profile_id", "affected_runs"}`.
pub fn delete_profile_checked(root: &Path, vault: &dyn Vault, id: &str,
                              check: &mut dyn FnMut(&str) -> Result<Value, String>, lock: &Mutex<()>)
    -> Result<Value, String> {
    prepare_delete(root, id)?;
    let affected_runs = edit_check_outcome(check(id))?;
    let _guard = lock.lock().map_err(|_| LOCK_FAILED)?;
    delete_profile(root, vault, id)?;
    Ok(json!({"profile_id": id, "affected_runs": affected_runs}))
}

#[cfg(test)]
pub(crate) mod testing {
    use super::Vault;
    use std::{collections::BTreeMap, path::{Path, PathBuf}, sync::{atomic::{AtomicUsize, Ordering}, Mutex}};

    /// An in-memory vault that records every name read.
    #[derive(Default)]
    pub struct MemoryVault { entries: Mutex<BTreeMap<String, String>>, reads: Mutex<Vec<String>> }

    impl MemoryVault {
        pub fn with(entries: &[(&str, &str)]) -> Self {
            let vault = Self::default();
            for (name, value) in entries { vault.set(name, value).unwrap(); }
            vault
        }
        /// Windows Credential Manager compares target names without case; so does this vault.
        fn key(name: &str) -> String { name.to_lowercase() }
        pub fn value(&self, name: &str) -> Option<String> { self.entries.lock().unwrap().get(&Self::key(name)).cloned() }
        pub fn names(&self) -> Vec<String> { self.entries.lock().unwrap().keys().cloned().collect() }
        pub fn read_names(&self) -> Vec<String> { self.reads.lock().unwrap().clone() }
        pub fn clear_reads(&self) { self.reads.lock().unwrap().clear(); }
    }

    impl Vault for MemoryVault {
        fn get(&self, name: &str) -> Result<Option<String>, String> {
            self.reads.lock().unwrap().push(name.to_owned());
            Ok(self.value(name))
        }
        fn set(&self, name: &str, value: &str) -> Result<(), String> {
            self.entries.lock().unwrap().insert(Self::key(name), value.to_owned());
            Ok(())
        }
        fn delete(&self, name: &str) -> Result<(), String> {
            self.entries.lock().unwrap().remove(&Self::key(name));
            Ok(())
        }
    }

    /// A vault whose every call fails, like a locked or unavailable Credential Manager.
    pub struct FailingVault;

    impl Vault for FailingVault {
        fn get(&self, _: &str) -> Result<Option<String>, String> { Err("Windows 자격 증명에 접근할 수 없습니다.".into()) }
        fn set(&self, _: &str, _: &str) -> Result<(), String> { Err("인증값 보안 저장 실패".into()) }
        fn delete(&self, _: &str) -> Result<(), String> { Err("인증값 삭제 실패".into()) }
    }

    /// Delegates to a memory vault but fails to delete one entry, like a single locked credential.
    pub struct FailingDelete<'a> { pub inner: &'a MemoryVault, pub name: String }

    impl Vault for FailingDelete<'_> {
        fn get(&self, name: &str) -> Result<Option<String>, String> { self.inner.get(name) }
        fn set(&self, name: &str, value: &str) -> Result<(), String> { self.inner.set(name, value) }
        fn delete(&self, name: &str) -> Result<(), String> {
            if name.eq_ignore_ascii_case(&self.name) { Err("인증값 삭제 실패".into()) } else { self.inner.delete(name) }
        }
    }

    /// A fresh directory under the system temp directory, removed on drop.
    pub struct TempRoot(PathBuf);

    impl TempRoot {
        pub fn new(label: &str) -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!("factory-native-{label}-{}-{}", std::process::id(),
                NEXT.fetch_add(1, Ordering::SeqCst)));
            let _ = std::fs::remove_dir_all(&path);
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        pub fn path(&self) -> &Path { &self.0 }
    }

    impl Drop for TempRoot {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    /// Synthetic credential values, built at runtime (the history-walking secret scan must never see a token shape).
    pub fn synthetic(label: &str) -> String { format!("synthetic-{label}-{}", "k".repeat(12)) }

    /// A fake PEM header assembled at runtime, as in main.rs, so no source file contains one.
    pub fn fake_key_header(kind: &str) -> String { format!("-----BEGIN {kind}{}-----", "PRIVATE KEY") }
}

#[cfg(test)]
mod tests {
    use super::testing::*;
    use super::*;

    fn obj(value: Value) -> Map<String, Value> { value.as_object().unwrap().clone() }
    fn runpod(name: &str, pod: &str) -> Map<String, Value> {
        obj(json!({"provider": "existing_runpod", "name": name, "pod_id": pod,
            "ssh_fingerprint": format!("SHA256:{}", "A".repeat(43)), "ssh_key_file": "C:\\keys\\id_ed25519"}))
    }
    fn server(name: &str) -> Map<String, Value> {
        obj(json!({"provider": "ssh_server", "name": name, "server_ssh_host": "gpu.example", "server_ssh_port": "22",
            "server_ssh_username": "researcher", "server_ssh_fingerprint": format!("SHA256:{}", "B".repeat(43)),
            "server_remote_root": "/home/researcher/factory", "server_ssh_key_file": "C:\\keys\\gpu_ed25519",
            "server_total_hourly_cost_usd": "0",
            "server_selected_gpu_uuids": "[\"GPU-11111111-2222-3333-4444-555555555555\"]"}))
    }
    fn with_key(mut values: Map<String, Value>, key: &str) -> Map<String, Value> {
        values.insert("RUNPOD_API_KEY".into(), json!(key));
        values
    }
    fn key_of(vault: &MemoryVault, id: &str) -> Option<String> { vault.value(&profile_secret_entry(id, "RUNPOD_API_KEY")) }
    fn file_bytes(root: &Path) -> Option<Vec<u8>> { fs::read(root.join(PROFILES_FILE)).ok() }
    fn answer_ok(affected: Value) -> Value { json!({"status": "ok", "issues": [], "data": {"affected_runs": affected}}) }
    fn answer_in_use() -> Value {
        json!({"status": "conflict", "data": null, "issues": [{"code": "profile_in_use", "path": "",
            "message": "이 실행 프로필로 실행 중이거나 결과 확인이 끝나지 않은 작업이 있어 수정·삭제할 수 없습니다."}]})
    }

    #[test]
    fn profile_ids_are_bounded_local_ids_and_estimate_prefix_is_reserved() {
        let longest = "a".repeat(MAX_PROFILE_ID);
        for id in ["a", "A1", "_x", "-x", "gpu.lab-1", "p_2", "estimate", longest.as_str()] {
            assert!(valid_profile_id(id), "{id} was refused");
        }
        let too_long = "a".repeat(MAX_PROFILE_ID + 1);
        for id in ["", ".hidden", "..", "a b", "a/b", "a\\b", "ä", "a:b", "a\nb", too_long.as_str(), "estimate-1",
            "Estimate-x", "ESTIMATE-gpu", "con", "NUL", "com1", "LPT9.x", "aux.lab"] {
            assert!(!valid_profile_id(id), "{id:?} was accepted");
        }
        assert_eq!(profile_secret_entry("gpu-1", "RUNPOD_API_KEY"), "executor-profile/gpu-1/RUNPOD_API_KEY");
        // Invalid IDs never reach the vault or the profile file.
        let root = TempRoot::new("ids");
        let vault = MemoryVault::default();
        assert!(save_profile(root.path(), &vault, "estimate-x", &runpod("x", "pod1"), None).is_err());
        assert!(save_profile(root.path(), &vault, "../x", &with_key(runpod("x", "pod1"), &synthetic("a")), None).is_err());
        assert!(delete_profile(root.path(), &vault, "a/b").is_err());
        assert_eq!(profile_secret(root.path(), &vault, "../x").unwrap(), None);
        assert!(vault.names().is_empty() && vault.read_names().is_empty());
        assert!(file_bytes(root.path()).is_none());
    }

    #[test]
    fn profile_ids_are_unique_ignoring_case_and_trailing_dots() {
        // Windows file names and Credential Manager targets ignore case, and file names drop trailing dots: 9.1
        // compares profile IDs with casefold + rstrip("."), so "GPU-A", "gpu-a" and "gpu-a." name one profile.
        assert_eq!(profile_id_key("GPU-A.."), "gpu-a");
        assert_eq!(profile_id_key("gpu-a"), profile_id_key("Gpu-A."));
        assert_ne!(profile_id_key("gpu-a.1"), profile_id_key("gpu-a"));
        let root = TempRoot::new("id-collide");
        let lock = Mutex::new(());
        let key = synthetic("gpu-a");
        let vault = MemoryVault::default();
        save_profile(root.path(), &vault, "GPU-A", &with_key(runpod("A", "pod-a"), &key), None).unwrap();
        let before = file_bytes(root.path());
        for id in ["gpu-a", "GPU-A.", "gpu-a..", "Gpu-A"] {
            let other = with_key(runpod("B", "pod-b"), &synthetic("other"));
            assert!(save_profile(root.path(), &vault, id, &other, None).is_err(), "{id} collided but was saved");
            assert!(save_profile_checked(root.path(), &vault, id, &other, false,
                &mut |_| panic!("the sidecar was asked about a colliding ID"), &lock).is_err());
            // Credential Manager would resolve this ID to GPU-A's entry: deleting it must not remove that key.
            assert!(delete_profile(root.path(), &vault, id).is_err(), "deleting {id} would remove GPU-A's key");
            assert!(delete_profile_checked(root.path(), &vault, id,
                &mut |_| panic!("the sidecar was asked about a colliding ID"), &lock).is_err());
            assert_eq!(profile_secret(root.path(), &vault, id).unwrap(), None, "{id} reached GPU-A's key");
        }
        assert_eq!(file_bytes(root.path()), before);
        assert_eq!(key_of(&vault, "GPU-A"), Some(key.clone()));
        // The exact ID still edits; IDs that differ in more than case or trailing dots are other profiles.
        save_profile(root.path(), &vault, "GPU-A", &obj(json!({"name": "A2"})), None).unwrap();
        save_profile(root.path(), &vault, "gpu-a.1", &runpod("A1", "pod-a1"), None).unwrap();
        save_profile(root.path(), &vault, "gpu_a", &runpod("A_", "pod-a_"), None).unwrap();
        assert_eq!(key_of(&vault, "GPU-A"), Some(key));
        // A hand-edited file with colliding IDs serves only the first one in ID order.
        fs::write(root.path().join(PROFILES_FILE), json!({"schema_version": 1, "profiles": {
            "A": runpod("first", "pod-1"), "a.": runpod("second", "pod-2"), "a": runpod("shadow", "pod-0"),
            "b": runpod("third", "pod-3")}}).to_string()).unwrap();
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        let ids: Vec<&str> = listed["profiles"].as_array().unwrap().iter().map(|p| p["profile_id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["A", "b"]);
        // The shadowed "a" names A's Credential Manager entry: only the served "A" may delete it.
        let owner = synthetic("owner-a");
        vault.set(&profile_secret_entry("A", "RUNPOD_API_KEY"), &owner).unwrap();
        for shadow in ["a", "a."] { assert!(delete_profile(root.path(), &vault, shadow).is_err(), "{shadow} was deleted"); }
        assert_eq!(key_of(&vault, "A"), Some(owner));
        delete_profile(root.path(), &vault, "A").unwrap();
        assert_eq!(key_of(&vault, "A"), None);
    }

    #[test]
    fn profile_fields_are_allowlisted_per_provider_and_provider_is_immutable() {
        assert!(merge_profile(None, &runpod("pod", "p1")).is_ok());
        assert!(merge_profile(None, &server("lab")).is_ok());
        let mut mixed = runpod("pod", "p1");
        mixed.insert("server_ssh_host".into(), json!("gpu.example"));
        assert!(merge_profile(None, &mixed).is_err(), "SSH fields were accepted on a RunPod profile");
        let mut mixed = server("lab");
        mixed.insert("pod_id".into(), json!("p1"));
        assert!(merge_profile(None, &mixed).is_err(), "RunPod fields were accepted on an SSH profile");
        // Unknown fields, secret names (the API key never passes through merge_profile) and non-strings.
        for (name, value) in [("unknown", json!("x")), ("RUNPOD_API_KEY", json!(synthetic("key"))),
            ("HF_TOKEN", json!("x")), ("MLFLOW_TRACKING_TOKEN", json!("x")), ("S3_SECRET_ACCESS_KEY", json!("x")),
            ("command", json!("calc.exe")), ("pod_id", json!(12)), ("name", json!(null)), ("name", json!(["a"])),
            ("ssh_fingerprint", json!(true)), ("name", json!("x".repeat(2049)))] {
            let mut input = runpod("pod", "p1");
            input.insert(name.into(), value);
            assert!(merge_profile(None, &input).is_err(), "{name} was accepted");
        }
        assert!(merge_profile(None, &obj(json!({"name": "x", "pod_id": "p1"}))).is_err(), "a profile without provider");
        for provider in ["", "runpod", "SSH_SERVER", " ssh_server", "existing_runpod "] {
            let mut input = runpod("pod", "p1");
            input.insert("provider".into(), json!(provider));
            assert!(merge_profile(None, &input).is_err(), "provider {provider:?} was accepted");
        }
        // A display name is required (the Python revision and the public profile use it).
        for name in ["", "   ", &"x".repeat(201)] {
            let mut input = runpod("pod", "p1");
            input.insert("name".into(), json!(name));
            assert!(merge_profile(None, &input).is_err(), "name {name:?} was accepted");
        }
        // The provider is fixed once saved; other fields merge into the stored ones.
        let existing = merge_profile(None, &runpod("pod", "p1")).unwrap();
        assert!(merge_profile(Some(&existing), &obj(json!({"provider": "ssh_server"}))).is_err());
        let renamed = merge_profile(Some(&existing), &obj(json!({"name": "renamed", "provider": "existing_runpod"}))).unwrap();
        assert_eq!(renamed.get("name"), Some(&json!("renamed")));
        assert_eq!(renamed.get("pod_id"), Some(&json!("p1")));
        // Through the store: a refused provider switch writes nothing.
        let root = TempRoot::new("provider");
        let vault = MemoryVault::default();
        save_profile(root.path(), &vault, "p", &runpod("pod", "p1"), None).unwrap();
        let before = file_bytes(root.path());
        assert!(save_profile(root.path(), &vault, "p", &server("lab"), None).is_err());
        assert_eq!(file_bytes(root.path()), before);
    }

    #[test]
    fn each_profile_keeps_its_own_secret_and_empty_input_preserves_it() {
        let root = TempRoot::new("own-secret");
        let legacy = synthetic("legacy-runpod");
        let vault = MemoryVault::with(&[("RUNPOD_API_KEY", &legacy)]);
        let (a, b) = (synthetic("profile-a"), synthetic("profile-b"));
        save_profile(root.path(), &vault, "a", &with_key(runpod("A", "pod-a"), &a), None).unwrap();
        save_profile(root.path(), &vault, "b", &with_key(runpod("B", "pod-b"), &b), None).unwrap();
        assert_eq!(key_of(&vault, "a"), Some(a.clone()));
        assert_eq!(key_of(&vault, "b"), Some(b.clone()));
        assert_eq!(vault.value("RUNPOD_API_KEY"), Some(legacy.clone()), "the legacy connection key changed");
        // An empty key input, or an edit without the key field, keeps the saved key.
        save_profile(root.path(), &vault, "a", &with_key(obj(json!({"name": "A2"})), ""), None).unwrap();
        save_profile(root.path(), &vault, "a", &obj(json!({"pod_id": "pod-a2"})), None).unwrap();
        assert_eq!(key_of(&vault, "a"), Some(a.clone()));
        // A new value replaces only that profile's key.
        let rotated = synthetic("profile-a-rotated");
        save_profile(root.path(), &vault, "a", &with_key(Map::new(), &rotated), None).unwrap();
        assert_eq!(key_of(&vault, "a"), Some(rotated.clone()));
        assert_eq!(key_of(&vault, "b"), Some(b.clone()));
        let listed = read_profiles(root.path(), &vault, true).unwrap();
        assert_eq!(listed["profiles"][0]["profile_id"], "a");
        assert_eq!(listed["profiles"][0]["fields"]["name"], "A2");
        assert_eq!(listed["profiles"][0]["fields"]["pod_id"], "pod-a2");
        assert_eq!(listed["profiles"][0]["secrets"], json!({"RUNPOD_API_KEY": rotated}));
        assert_eq!(listed["profiles"][1]["secrets"], json!({"RUNPOD_API_KEY": b}));
        assert_eq!(profile_secret(root.path(), &vault, "b").unwrap(), Some(b.clone()));
        assert_eq!(profile_secret(root.path(), &vault, "missing").unwrap(), None);
        // SSH profiles hold no RunPod key: a typed key is refused, an empty one is ignored.
        assert!(save_profile(root.path(), &vault, "lab", &with_key(server("lab"), &synthetic("x")), None).is_err());
        save_profile(root.path(), &vault, "lab", &with_key(server("lab"), ""), None).unwrap();
        assert_eq!(key_of(&vault, "lab"), None);
        assert_eq!(read_profiles(root.path(), &vault, true).unwrap()["profiles"][2]["secrets"], json!({}));
        // A key value must be a bounded string.
        assert!(save_profile(root.path(), &vault, "a", &obj(json!({"RUNPOD_API_KEY": 12})), None).is_err());
        assert!(save_profile(root.path(), &vault, "a", &with_key(Map::new(), &"k".repeat(2049)), None).is_err());
        assert_eq!(key_of(&vault, "a"), Some(rotated));
    }

    #[test]
    fn public_profile_views_never_contain_secret_values() {
        let root = TempRoot::new("public-view");
        let key = synthetic("profile-a");
        let vault = MemoryVault::default();
        let saved = save_profile(root.path(), &vault, "a", &with_key(runpod("A", "pod-a"), &key), None).unwrap();
        save_profile(root.path(), &vault, "lab", &server("lab"), None).unwrap();
        let file = String::from_utf8(file_bytes(root.path()).unwrap()).unwrap();
        let public = read_profiles(root.path(), &vault, false).unwrap();
        for text in [&file, &public.to_string(), &saved.to_string()] {
            assert!(!text.contains(&key), "a secret value is visible");
            assert!(!text.contains("\"RUNPOD_API_KEY\"") && !text.contains("\"secrets\""), "{text}");
        }
        assert_eq!(saved, json!({"profile_id": "a", "fields": runpod("A", "pod-a"), "RUNPOD_API_KEY_saved": true}));
        assert_eq!(public, json!({"profiles": [
            {"profile_id": "a", "fields": runpod("A", "pod-a"), "RUNPOD_API_KEY_saved": true},
            {"profile_id": "lab", "fields": server("lab"), "RUNPOD_API_KEY_saved": false}]}));
        let stored: Value = serde_json::from_str(&file).unwrap();
        assert_eq!(stored, json!({"schema_version": 1, "profiles": {"a": runpod("A", "pod-a"), "lab": server("lab")}}));
    }

    #[test]
    fn profile_key_paths_refuse_pasted_keys_and_ppk_files() {
        for (fields, name) in [(runpod("p", "pod"), "ssh_key_file"), (server("lab"), "server_ssh_key_file")] {
            for pasted in [format!("{}\nb3BlbnNzaC1rZXktdjE=", fake_key_header("OPENSSH ")), fake_key_header("RSA "),
                "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none".into(), "Public-Lines: 2\nAAAA\nPrivate-Lines: 1\nAAAA".into()] {
                let mut input = fields.clone();
                input.insert(name.into(), json!(pasted));
                let error = merge_profile(None, &input).expect_err("a pasted private key was accepted");
                assert!(error.contains("파일 경로"), "{error}");
            }
            for path in ["C:\\keys\\server.ppk", "C:\\keys\\SERVER.PPK", "D:\\keys\\gpu.Ppk", "C:\\keys\\server.ppk. ."] {
                let mut input = fields.clone();
                input.insert(name.into(), json!(path));
                let error = merge_profile(None, &input).expect_err("a PuTTY .ppk path was accepted");
                assert!(error.contains("PuTTY .ppk") && error.contains("OpenSSH"), "{error}");
            }
            for path in ["C:\\keys\\id_ed25519", "C:\\keys\\ppk\\id_rsa", "C:\\keys\\server.ppk.pem"] {
                let mut input = fields.clone();
                input.insert(name.into(), json!(path));
                assert!(merge_profile(None, &input).is_ok(), "{path} was refused");
            }
        }
        // Key content is refused in every profile field (stricter than settings); ordinary KEY words are fine.
        for name in ["name", "pod_id", "ssh_fingerprint"] {
            for pasted in [fake_key_header("OPENSSH "), format!("lab {}", "PRIVATE KEY"), "PuTTY-User-Key-File-2".into()] {
                let mut input = runpod("p", "pod");
                input.insert(name.into(), json!(pasted));
                assert!(merge_profile(None, &input).is_err(), "key content was accepted in {name}");
            }
        }
        for name in ["API KEY rotation lab", "Private key server", "KEY-1 pod", "Monkey business"] {
            let mut input = runpod("p", "pod");
            input.insert("name".into(), json!(name));
            assert!(merge_profile(None, &input).is_ok(), "the name {name:?} was refused");
        }
        // A refused key path stores neither the profile nor its API key.
        let root = TempRoot::new("pasted");
        let vault = MemoryVault::default();
        let mut input = with_key(runpod("p", "pod"), &synthetic("key"));
        input.insert("ssh_key_file".into(), json!(fake_key_header("OPENSSH ")));
        assert!(save_profile(root.path(), &vault, "p", &input, None).is_err());
        assert!(vault.names().is_empty() && file_bytes(root.path()).is_none());
        // Key content in a hand-edited file is never served.
        let mut edited = runpod("h", "pod");
        edited.insert("ssh_key_file".into(), json!(fake_key_header("OPENSSH ")));
        fs::write(root.path().join(PROFILES_FILE), json!({"schema_version": 1, "profiles": {"h": edited}}).to_string()).unwrap();
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        assert!(!listed.to_string().contains("PRIVATE KEY"));
        assert_eq!(listed["profiles"][0]["profile_id"], "h");
        assert!(listed["profiles"][0]["fields"].get("ssh_key_file").is_none());
    }

    #[test]
    fn profile_key_paths_must_be_absolute() {
        // The D11 transport hash resolves the key path: a relative path would resolve against different working
        // directories in the planning sidecar and in the controller, and every real run would be refused for good.
        for (fields, name) in [(runpod("p", "pod"), "ssh_key_file"), (server("lab"), "server_ssh_key_file")] {
            // UNC and device paths are absolute but network or device access (Task 9 D12, 9.7 review Minor 7).
            for path in ["id_ed25519", ".ssh\\id_ed25519", "..\\keys\\id", "./id", "\\keys\\id_ed25519", "C:keys\\id",
                " C:\\keys\\id_ed25519", "\\\\server\\share\\id", "//server/share/id", "\\/server/share/id",
                "\\\\?\\C:\\keys\\id", "\\\\.\\pipe\\id", "\\\\?\\UNC\\server\\share\\id"] {
                let mut input = fields.clone();
                input.insert(name.into(), json!(path));
                let error = merge_profile(None, &input).expect_err("a relative key path was accepted");
                assert!(error.contains("전체 경로"), "{path}: {error}");
            }
            for path in ["C:\\Users\\researcher\\.ssh\\id_ed25519", "D:/keys/id_rsa", ""] {
                let mut input = fields.clone();
                input.insert(name.into(), json!(path));
                assert!(merge_profile(None, &input).is_ok(), "{path:?} was refused");
            }
        }
        // A stored relative path (hand-edited) must be replaced on the next save of that profile.
        let root = TempRoot::new("relative-key");
        let vault = MemoryVault::default();
        let mut edited = runpod("h", "pod");
        edited.insert("ssh_key_file".into(), json!("id_ed25519"));
        fs::write(root.path().join(PROFILES_FILE), json!({"schema_version": 1, "profiles": {"h": edited}}).to_string()).unwrap();
        assert!(save_profile(root.path(), &vault, "h", &obj(json!({"name": "renamed"})), None).is_err());
        assert!(save_profile(root.path(), &vault, "h", &obj(json!({"ssh_key_file": "C:\\keys\\id_ed25519"})), None).is_ok());
        // The legacy connection settings keep accepting any key path, as before.
        for name in ["ssh_key_file", "server_ssh_key_file"] {
            assert!(crate::merge_public_settings(json!({}), json!({name: "id_ed25519"}).as_object().unwrap()).is_ok());
        }
    }

    #[test]
    fn profile_gpu_selection_and_cost_are_validated() {
        let gpus = |count: usize| format!("[{}]", (0..count).map(|i| format!("\"GPU-{i}\"")).collect::<Vec<_>>().join(","));
        let (eight, nine) = (gpus(8), gpus(9));
        for (name, value) in [("server_selected_gpu_uuids", "GPU-1"), ("server_selected_gpu_uuids", "[1]"),
            ("server_selected_gpu_uuids", "{\"uuid\":\"GPU-1\"}"), ("server_selected_gpu_uuids", "[\"GPU-1\""),
            ("server_selected_gpu_uuids", "null"), ("server_selected_gpu_uuids", nine.as_str()),
            ("server_selected_gpu_uuids", "[\"GPU-1\",\"GPU-1\"]"),
            ("server_total_hourly_cost_usd", "-1"), ("server_total_hourly_cost_usd", "nan"),
            ("server_total_hourly_cost_usd", "inf"), ("server_total_hourly_cost_usd", "1e3"),
            ("server_total_hourly_cost_usd", " 1"), ("server_total_hourly_cost_usd", "1.2.3"),
            ("server_total_hourly_cost_usd", "$2"), ("server_total_hourly_cost_usd", "."),
            ("server_total_hourly_cost_usd", "1."), ("server_ssh_port", "0"), ("server_ssh_port", "65536"),
            ("server_ssh_port", "22a"), ("server_ssh_port", "-22"), ("server_ssh_port", "+22")] {
            let mut input = server("lab");
            input.insert(name.into(), json!(value));
            assert!(merge_profile(None, &input).is_err(), "{name}={value} was accepted");
        }
        for (name, value) in [("server_selected_gpu_uuids", ""), ("server_selected_gpu_uuids", "[]"),
            ("server_selected_gpu_uuids", eight.as_str()), ("server_total_hourly_cost_usd", "0"),
            ("server_total_hourly_cost_usd", "2.75"), ("server_total_hourly_cost_usd", "12"),
            ("server_total_hourly_cost_usd", ""), ("server_ssh_port", "2222"), ("server_ssh_port", "")] {
            let mut input = server("lab");
            input.insert(name.into(), json!(value));
            assert!(merge_profile(None, &input).is_ok(), "{name}={value} was refused");
        }
    }

    #[test]
    fn deleting_a_profile_removes_its_secret_only() {
        let root = TempRoot::new("delete");
        let legacy: Vec<(String, String)> = crate::SECRETS.iter().map(|name| (name.to_string(), synthetic(name))).collect();
        let entries: Vec<(&str, &str)> = legacy.iter().map(|(name, value)| (name.as_str(), value.as_str())).collect();
        let vault = MemoryVault::with(&entries);
        save_profile(root.path(), &vault, "a", &with_key(runpod("A", "pod-a"), &synthetic("a")), None).unwrap();
        save_profile(root.path(), &vault, "b", &with_key(runpod("B", "pod-b"), &synthetic("b")), None).unwrap();
        delete_profile(root.path(), &vault, "a").unwrap();
        assert_eq!(key_of(&vault, "a"), None);
        assert_eq!(key_of(&vault, "b"), Some(synthetic("b")));
        for (name, value) in &legacy { assert_eq!(vault.value(name).as_ref(), Some(value), "{name} was removed"); }
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        assert_eq!(listed["profiles"].as_array().unwrap().len(), 1);
        assert_eq!(listed["profiles"][0]["profile_id"], "b");
        // Deleting again, or a profile that never existed, is a no-op.
        delete_profile(root.path(), &vault, "a").unwrap();
        delete_profile(root.path(), &vault, "never").unwrap();
        assert_eq!(read_profiles(root.path(), &vault, false).unwrap(), listed);
    }

    #[test]
    fn forget_credentials_removes_profile_secrets_too() {
        let root = TempRoot::new("forget");
        let entries: Vec<(String, String)> = crate::SECRETS.iter().map(|name| (name.to_string(), synthetic(name))).collect();
        let entries: Vec<(&str, &str)> = entries.iter().map(|(name, value)| (name.as_str(), value.as_str())).collect();
        let vault = MemoryVault::with(&entries);
        save_profile(root.path(), &vault, "a", &with_key(runpod("A", "pod-a"), &synthetic("a")), None).unwrap();
        save_profile(root.path(), &vault, "b", &with_key(runpod("B", "pod-b"), &synthetic("b")), None).unwrap();
        save_profile(root.path(), &vault, "lab", &server("lab"), None).unwrap();
        crate::forget_all_credentials(|| Ok(root.path().to_path_buf()), &vault).unwrap();
        assert!(vault.names().is_empty(), "left behind: {:?}", vault.names());
        // Profiles keep their public fields; only the credentials are forgotten.
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        assert_eq!(listed["profiles"].as_array().unwrap().len(), 3);
        assert!(listed["profiles"].as_array().unwrap().iter().all(|p| p["RUNPOD_API_KEY_saved"] == false));
        // Without a profile file the legacy credentials are still forgotten.
        let empty = TempRoot::new("forget-empty");
        let vault = MemoryVault::with(&entries);
        crate::forget_all_credentials(|| Ok(empty.path().to_path_buf()), &vault).unwrap();
        assert!(vault.names().is_empty());
    }

    #[test]
    fn profile_store_writes_replace_the_file_atomically() {
        // A concurrent reader or a crash must see the old or the new store, never a partial file: the store is written
        // to a temporary file and renamed over the old one, so a second link to the old file keeps its bytes.
        let root = TempRoot::new("atomic");
        let vault = MemoryVault::default();
        save_profile(root.path(), &vault, "a", &runpod("A", "pod-a"), None).unwrap();
        let file = root.path().join(PROFILES_FILE);
        let before = fs::read(&file).unwrap();
        let reader = root.path().join("reader-view.json");
        fs::hard_link(&file, &reader).unwrap();
        save_profile(root.path(), &vault, "b", &runpod("B", "pod-b"), None).unwrap();
        assert_eq!(fs::read(&reader).unwrap(), before, "the store was rewritten in place instead of replaced");
        let stored: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
        assert_eq!(stored["profiles"].as_object().unwrap().len(), 2);
        assert!(!root.path().join("executor-profiles.tmp").exists());
    }

    #[test]
    fn a_failed_store_write_restores_the_previous_vault_state() {
        let root = TempRoot::new("rollback");
        let vault = MemoryVault::default();
        let (first, second) = (synthetic("first"), synthetic("second"));
        save_profile(root.path(), &vault, "b", &with_key(runpod("B", "pod-b"), &first), None).unwrap();
        let before = file_bytes(root.path());
        // A directory where the temporary file belongs makes every store write fail after the vault write.
        fs::create_dir(root.path().join("executor-profiles.tmp")).unwrap();
        assert!(save_profile(root.path(), &vault, "a", &with_key(runpod("A", "pod-a"), &synthetic("new")), None).is_err());
        assert_eq!(key_of(&vault, "a"), None, "a new profile's key was orphaned");
        assert!(save_profile(root.path(), &vault, "b", &with_key(Map::new(), &second), None).is_err());
        assert_eq!(key_of(&vault, "b"), Some(first), "an existing profile's key no longer matches its file");
        assert_eq!(file_bytes(root.path()), before);
    }

    #[test]
    fn store_writes_keep_entries_they_cannot_serve_so_no_key_is_orphaned() {
        let root = TempRoot::new("keep-unserved");
        let (bad_key, shadow_key) = (synthetic("bad"), synthetic("shadow"));
        let (bad_entry, shadow_entry) = (profile_secret_entry("bad", "RUNPOD_API_KEY"), profile_secret_entry("ok.", "RUNPOD_API_KEY"));
        let vault = MemoryVault::with(&[(&bad_entry, &bad_key), (&shadow_entry, &shadow_key)]);
        let mut pasted = runpod("x", "pod");
        pasted.insert("ssh_key_file".into(), json!(fake_key_header("OPENSSH ")));
        fs::write(root.path().join(PROFILES_FILE), json!({"schema_version": 1, "profiles": {"ok": runpod("ok", "pod-ok"),
            "ok.": runpod("shadowed", "pod-s"), "bad": {"provider": "runpod", "name": "b"}, "estimate-x": pasted,
            "list": [1]}}).to_string()).unwrap();
        save_profile(root.path(), &vault, "new", &runpod("N", "pod-n"), None).unwrap();
        let stored: Value = serde_json::from_slice(&fs::read(root.path().join(PROFILES_FILE)).unwrap()).unwrap();
        let profiles = stored["profiles"].as_object().unwrap();
        // Entries the app cannot serve stay, so forget_credentials still finds their keys; key content is not kept.
        assert_eq!(profiles["bad"], json!({"provider": "runpod", "name": "b"}));
        assert_eq!(profiles["ok."], json!(runpod("shadowed", "pod-s")));
        assert_eq!(profiles["list"], json!([1]));
        assert_eq!(profiles["estimate-x"]["name"], "x");
        assert!(profiles["estimate-x"].get("ssh_key_file").is_none() && !stored.to_string().contains("PRIVATE KEY"));
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        let ids: Vec<&str> = listed["profiles"].as_array().unwrap().iter().map(|p| p["profile_id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["new", "ok"]);
        // IDs that Windows would treat as an unserved entry's name are refused too (they share its vault entry).
        assert!(save_profile(root.path(), &vault, "BAD", &runpod("B", "pod-b"), None).is_err());
        assert!(delete_profile(root.path(), &vault, "Bad").is_err());
        assert_eq!(vault.value(&bad_entry), Some(bad_key));
        forget_profile_secrets(root.path(), &vault).unwrap();
        assert_eq!(vault.value(&bad_entry), None);
        assert_eq!(vault.value(&shadow_entry), None);
    }

    #[test]
    fn forgetting_profile_keys_continues_past_a_failing_entry() {
        let root = TempRoot::new("forget-continue");
        let vault = MemoryVault::default();
        for id in ["a", "b", "c"] {
            save_profile(root.path(), &vault, id, &with_key(runpod(id, "pod"), &synthetic(id)), None).unwrap();
        }
        let flaky = FailingDelete { inner: &vault, name: profile_secret_entry("b", "RUNPOD_API_KEY") };
        assert!(forget_profile_secrets(root.path(), &flaky).is_err());
        assert_eq!(key_of(&vault, "a"), None);
        assert_eq!(key_of(&vault, "c"), None);
        assert_eq!(key_of(&vault, "b"), Some(synthetic("b")));
    }

    #[test]
    fn stale_vault_entries_never_reach_ssh_profiles() {
        let root = TempRoot::new("stale-ssh");
        let stale = synthetic("stale");
        let vault = MemoryVault::with(&[(&profile_secret_entry("lab", "RUNPOD_API_KEY"), &stale)]);
        save_profile(root.path(), &vault, "lab", &server("lab"), None).unwrap();
        assert_eq!(profile_secret(root.path(), &vault, "lab").unwrap(), None, "a stale key reached an SSH profile");
        let listed = read_profiles(root.path(), &vault, true).unwrap();
        assert_eq!(listed["profiles"][0]["secrets"], json!({}));
        assert_eq!(listed["profiles"][0]["RUNPOD_API_KEY_saved"], false);
        assert!(!listed.to_string().contains(&stale));
    }

    #[test]
    fn legacy_forget_succeeds_whatever_the_profile_file_holds() {
        let legacy: Vec<(String, String)> = crate::SECRETS.iter().map(|name| (name.to_string(), synthetic(name))).collect();
        let a_entry = profile_secret_entry("a", "RUNPOD_API_KEY");
        let mut entries: Vec<(&str, &str)> = legacy.iter().map(|(name, value)| (name.as_str(), value.as_str())).collect();
        let a_key = synthetic("a");
        entries.push((&a_entry, &a_key));
        let unexpected = json!({"schema_version": 2, "profiles": {"a": runpod("A", "pod-a")}}).to_string();
        for (text, enumerable) in [("not json", false), ("[]", false), (unexpected.as_str(), true)] {
            let root = TempRoot::new("forget-corrupt");
            fs::write(root.path().join(PROFILES_FILE), text).unwrap();
            let vault = MemoryVault::with(&entries);
            let outcome = crate::forget_all_credentials(|| Ok(root.path().to_path_buf()), &vault).unwrap();
            assert_eq!(outcome["profile_credentials_forgotten"], false, "{text}");
            assert!(outcome["profile_warning"].as_str().unwrap().contains("실행 프로필"), "{outcome}");
            for name in crate::SECRETS { assert_eq!(vault.value(name), None, "{text}: {name} was kept"); }
            assert_eq!(vault.value(&a_entry).is_none(), enumerable, "{text}");
        }
        // An app folder that does not exist yet has nothing more to forget and is not created.
        let missing = std::env::temp_dir().join(format!("factory-native-missing-{}", std::process::id()));
        let vault = MemoryVault::with(&entries);
        assert_eq!(crate::forget_all_credentials(|| Ok(missing.clone()), &vault).unwrap(),
            json!({"profile_credentials_forgotten": true}));
        assert!(!missing.exists());
        // An app folder that cannot be resolved still forgets the legacy credentials and reports the rest.
        let vault = MemoryVault::with(&entries);
        let outcome = crate::forget_all_credentials(|| Err("앱 저장 폴더를 찾을 수 없습니다.".into()), &vault).unwrap();
        assert_eq!(outcome["profile_credentials_forgotten"], false);
        assert!(crate::SECRETS.iter().all(|name| vault.value(name).is_none()));
        // A legacy delete failure is still an error, as before.
        assert!(crate::forget_all_credentials(|| Ok(missing.clone()), &FailingVault).is_err());
    }

    #[test]
    fn profile_edits_wait_for_the_sidecar_edit_check() {
        let root = TempRoot::new("edit-check");
        let lock = Mutex::new(());
        let (first, second) = (synthetic("first"), synthetic("second"));
        let vault = MemoryVault::default();
        save_profile(root.path(), &vault, "a", &with_key(runpod("A", "pod-a"), &first), None).unwrap();
        let before = file_bytes(root.path());
        let edit = with_key(obj(json!({"name": "A2"})), &second);
        // A profile in use (9.3 `profile_in_use`) refuses the save and the delete with the sidecar's message.
        let mut asked = Vec::new();
        let error = save_profile_checked(root.path(), &vault, "a", &edit, false,
            &mut |id| { asked.push(id.to_owned()); Ok(answer_in_use()) }, &lock).unwrap_err();
        assert!(error.contains("수정·삭제할 수 없습니다"), "{error}");
        assert_eq!(asked, ["a"]);
        let error = delete_profile_checked(root.path(), &vault, "a", &mut |_| Ok(answer_in_use()), &lock).unwrap_err();
        assert!(error.contains("수정·삭제할 수 없습니다"), "{error}");
        assert_eq!(file_bytes(root.path()), before);
        assert_eq!(key_of(&vault, "a"), Some(first.clone()));
        // Fail closed: a missing check (9.3 not installed), an unexpected answer or a sidecar error refuses too.
        let missing = json!({"status": "invalid", "data": null,
            "issues": [{"code": "not_available", "path": "", "message": "이 기능은 아직 설치되지 않았습니다."}]});
        for answer in [Ok(missing), Ok(json!({"unexpected": true})), Ok(json!({"status": "ok"})), Err("엔진 응답 오류".to_string())] {
            assert!(save_profile_checked(root.path(), &vault, "a", &edit, false, &mut |_| answer.clone(), &lock).is_err());
            assert!(delete_profile_checked(root.path(), &vault, "a", &mut |_| answer.clone(), &lock).is_err());
        }
        assert_eq!(file_bytes(root.path()), before);
        assert_eq!(key_of(&vault, "a"), Some(first.clone()));
        // Invalid input is refused before the sidecar is asked.
        let mut asked = false;
        assert!(save_profile_checked(root.path(), &vault, "a", &obj(json!({"provider": "ssh_server"})), false,
            &mut |_| { asked = true; Ok(answer_ok(json!([]))) }, &lock).is_err());
        assert!(save_profile_checked(root.path(), &vault, "estimate-a", &runpod("x", "p"), false,
            &mut |_| { asked = true; Ok(answer_ok(json!([]))) }, &lock).is_err());
        assert!(!asked, "the sidecar was asked about an invalid edit");
        // An `ok` answer saves and reports the runs that must be planned again.
        let saved = save_profile_checked(root.path(), &vault, "a", &edit, false,
            &mut |_| Ok(answer_ok(json!(["0123456789abcdef0123456789abcdef"]))), &lock).unwrap();
        assert_eq!(saved["affected_runs"], json!(["0123456789abcdef0123456789abcdef"]));
        assert_eq!(saved["profile"]["fields"]["name"], "A2");
        assert_eq!(saved["profile"]["RUNPOD_API_KEY_saved"], true);
        assert!(!saved.to_string().contains(&second));
        assert_eq!(key_of(&vault, "a"), Some(second));
        let deleted = delete_profile_checked(root.path(), &vault, "a", &mut |_| Ok(answer_ok(json!([]))), &lock).unwrap();
        assert_eq!(deleted, json!({"profile_id": "a", "affected_runs": []}));
        assert_eq!(key_of(&vault, "a"), None);
    }

    #[test]
    fn the_legacy_runpod_key_is_copied_only_on_request() {
        let root = TempRoot::new("legacy-copy");
        let lock = Mutex::new(());
        let legacy = synthetic("legacy-runpod");
        let vault = MemoryVault::with(&[("RUNPOD_API_KEY", &legacy)]);
        let mut ok = |_: &str| Ok(answer_ok(json!([])));
        save_profile_checked(root.path(), &vault, "a", &runpod("A", "pod-a"), false, &mut ok, &lock).unwrap();
        assert_eq!(key_of(&vault, "a"), None, "the legacy key was copied without a request");
        let saved = save_profile_checked(root.path(), &vault, "a", &Map::new(), true, &mut ok, &lock).unwrap();
        assert_eq!(saved["profile"]["RUNPOD_API_KEY_saved"], true);
        assert_eq!(key_of(&vault, "a"), Some(legacy.clone()));
        assert_eq!(vault.value("RUNPOD_API_KEY"), Some(legacy.clone()), "the legacy entry must stay");
        // Refused: an SSH profile, a typed key together with the copy, and no legacy key to copy.
        assert!(save_profile_checked(root.path(), &vault, "lab", &server("lab"), true, &mut ok, &lock).is_err());
        assert_eq!(key_of(&vault, "lab"), None);
        assert!(save_profile_checked(root.path(), &vault, "a", &with_key(Map::new(), &synthetic("typed")), true,
            &mut ok, &lock).is_err());
        assert_eq!(key_of(&vault, "a"), Some(legacy));
        let other = TempRoot::new("legacy-missing");
        let empty = MemoryVault::default();
        assert!(save_profile_checked(other.path(), &empty, "b", &runpod("B", "pod-b"), true, &mut ok, &lock).is_err());
        assert!(file_bytes(other.path()).is_none() && empty.names().is_empty());
    }

    #[test]
    fn the_profile_store_is_bounded_and_refuses_a_corrupt_file() {
        let root = TempRoot::new("bounded");
        let vault = MemoryVault::default();
        for index in 0..MAX_PROFILES {
            save_profile(root.path(), &vault, &format!("p{index:02}"), &runpod("P", "pod"), None).unwrap();
        }
        assert!(save_profile(root.path(), &vault, "one-more", &runpod("P", "pod"), None).is_err());
        save_profile(root.path(), &vault, "p00", &obj(json!({"name": "edited"})), None).unwrap();
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        let ids: Vec<&str> = listed["profiles"].as_array().unwrap().iter().map(|p| p["profile_id"].as_str().unwrap()).collect();
        assert_eq!(ids.len(), MAX_PROFILES);
        assert!(ids.windows(2).all(|pair| pair[0] < pair[1]), "profiles are not sorted by ID");
        for text in ["not json", "[]", "{\"schema_version\": 2, \"profiles\": {}}", "{\"schema_version\": 1, \"profiles\": []}",
            "{\"profiles\": {}}"] {
            fs::write(root.path().join(PROFILES_FILE), text).unwrap();
            assert!(read_profiles(root.path(), &vault, false).is_err(), "{text} was read");
            assert!(save_profile(root.path(), &vault, "x", &runpod("X", "pod"), None).is_err(), "{text} was overwritten");
            assert!(forget_profile_secrets(root.path(), &vault).is_err());
            assert_eq!(fs::read_to_string(root.path().join(PROFILES_FILE)).unwrap(), text);
        }
        // Hand-edited entries that cannot be profiles are ignored; stray fields are not served.
        let mut stray = runpod("ok", "pod");
        stray.insert("unknown".into(), json!("x"));
        stray.insert("pod_id".into(), json!(12));
        fs::write(root.path().join(PROFILES_FILE), json!({"schema_version": 1, "profiles": {
            "estimate-x": runpod("e", "pod"), "bad": {"provider": "runpod", "name": "b"}, "ok": stray, "list": []}}).to_string()).unwrap();
        let listed = read_profiles(root.path(), &vault, false).unwrap();
        assert_eq!(listed["profiles"].as_array().unwrap().len(), 1);
        let mut expected = runpod("ok", "pod");
        expected.remove("pod_id");
        assert_eq!(listed["profiles"][0], json!({"profile_id": "ok", "fields": expected, "RUNPOD_API_KEY_saved": false}));
    }
}
