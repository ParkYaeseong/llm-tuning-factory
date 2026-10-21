type Obj = Record<string, any>;

export const TUTORIAL_DATASET = { id: "tutorial_sample", version: "1" } as const;

export interface TutorialContext {
  state: Obj;
  loadSample(): Promise<Obj>;
  normalizeConfig(): void;
  checkCapabilities(): Promise<unknown>;
  navigate(page: string): Promise<void>;
  finish(): Promise<void>;
}

export interface TutorialStep {
  id: string;
  page: string;
  /** Value of a data-tour attribute rendered on `page`; never an arbitrary selector. */
  anchor: string;
  title: string;
  body: string;
  kind: "explain" | "practice";
  done?(state: Obj, sample: Obj | null): boolean;
  enter?(context: TutorialContext): Promise<void>;
  /** `run` returns the state to apply; the engine drops it when the tour ended or restarted meanwhile. */
  cta?: { label: string; run(context: TutorialContext): Promise<Obj | void> };
}

const practiceData = (id: unknown, version: unknown) => id === TUTORIAL_DATASET.id && version === TUTORIAL_DATASET.version;

export const tutorialSteps: TutorialStep[] = [
  { id: "welcome", page: "overview", anchor: "overview-setup", kind: "explain", title: "작업 공간 둘러보기",
    body: "왼쪽 메뉴의 워크스페이스에서는 프로젝트 → 데이터 → 레시피 → 그래프 실행 순서로 여러 학습을 이어서 진행하고, 단일 학습(학습 설정 · 실행 관리)에서는 학습 하나를 바로 계획합니다. 오른쪽 ‘시작하기’ 목록은 아직 남은 준비 단계를 보여 줍니다.\n이 튜토리얼은 약 7분 걸립니다. 단일 학습으로 실습한 뒤 프로젝트 화면을 둘러보며, GPU를 쓰거나 데이터를 전송하지 않습니다." },
  { id: "connection-provider", page: "connections", anchor: "connection-provider", kind: "explain", title: "GPU 연결 방식 고르기",
    body: "RunPod는 관리자가 배정한 Pod를 RunPod API로 확인한 뒤 SSH로 학습합니다. GPU 서버는 BOP 같은 기관 Linux·NVIDIA 서버에 SSH로 직접 접속해 학습합니다.\n배정받은 자원에 맞는 방식을 고르세요. 앱은 Pod나 서버를 켜거나 끄지 않습니다." },
  { id: "connection-fields", page: "connections", anchor: "connection-fields", kind: "explain", title: "연결에 필요한 정보",
    body: "RunPod는 API 키와 Pod ID, GPU 서버는 주소·포트·계정, SSH 개인키 파일(OpenSSH 형식·암호 없음), 작업 폴더, 시간당 비용(내부 서버는 0 가능)이 필요합니다.\n두 방식 모두 SSH 서버 지문은 관리자에게 신뢰할 수 있는 경로로 받은 값만 입력하세요. 인증값은 Windows 자격 증명 저장소에 보관됩니다. 지금 입력하지 않아도 튜토리얼은 계속됩니다." },
  { id: "connection-runtime", page: "connections", anchor: "connection-runtime", kind: "explain", title: "학습 환경 확인과 준비",
    body: "‘환경 확인’은 GPU와 학습 엔진 상태를 조회만 합니다. ‘학습 환경 준비’는 확인 창을 거친 뒤 원격의 앱 전용 폴더에 학습 엔진을 설치하며, 그동안 GPU 요금이 발생할 수 있습니다.\nGPU 서버에서는 ‘GPU 목록 조회’로 GPU 하나를 골라 ‘GPU 선택 저장’을 누릅니다." },
  { id: "data-preview", page: "datasets", anchor: "dataset-form", kind: "practice", title: "연습 데이터 미리보기",
    body: "‘연습 샘플 채우기’를 누르면 앱에 포함된 합성 예제 8건이 양식에 채워집니다. 이어서 양식의 ‘샘플 미리보기’를 눌러 형식과 건수를 확인하세요.\n미리보기는 이 PC에서만 실행됩니다.",
    cta: { label: "연습 샘플 채우기", async run(context) {
      const sample = await context.loadSample();
      return { datasetDraft: { dataset_id: sample.dataset_id, version: sample.version, kind: sample.kind,
        path: sample.path, synthetic: true, export_allowed: false }, preview: null };
    } },
    done: (state, sample) => Boolean(sample && state.preview?.rows === sample.rows && state.datasetDraft?.path === sample.path) },
  { id: "data-register", page: "datasets", anchor: "dataset-submit", kind: "practice", title: "연습 데이터 등록",
    body: "‘검사하고 등록’을 눌러 연습 데이터를 이 PC에 등록하세요. 등록한 버전은 바뀌지 않는 사본으로 보관됩니다.\n연습 데이터는 합성 표시가 붙고 GPU 전송이 막혀 있어 실제 학습에는 쓸 수 없습니다. 이미 등록했다면 바로 다음으로 넘어갈 수 있습니다.",
    done: state => (state.catalog?.datasets || []).some((d: Obj) => practiceData(d.dataset_id, d.version)) },
  { id: "training-dataset", page: "training", anchor: "train-dataset", kind: "practice", title: "학습 데이터 선택",
    body: "예제 설정 ‘paper_extraction’을 불러왔습니다. ‘학습 데이터’에서 tutorial_sample · v1을 선택하세요.\n학습 단계의 SFT는 질문·답변 예시로 배우는 지도 학습입니다. 학습 방법의 LoRA는 모델 전체 대신 작은 추가 가중치만 학습해 GPU 메모리를 아끼고, QLoRA는 모델을 4비트로 불러와 메모리를 더 줄입니다.",
    async enter(context) {
      const templates: Obj[] = context.state.catalog?.templates || [];
      const template = templates.find(t => t.name === "paper_extraction") || templates[0];
      if (!template) throw new Error("예제 학습 설정을 찾지 못했습니다.");
      context.state.config = structuredClone(template.config);
      context.normalizeConfig();
      context.state.plan = null; context.state.capabilities = null;
      await context.checkCapabilities();
    },
    done: state => practiceData(state.config?.dataset_id, state.config?.dataset_version) },
  { id: "training-plan", page: "training", anchor: "training-plan", kind: "practice", title: "실행 계획 확인",
    body: "‘실행 계획 확인’을 누르세요. 설정 호환성을 검사한 뒤 확정된 설정과 예상 비용을 계산합니다.\n이 단계는 GPU를 시작하거나 데이터를 보내지 않습니다.",
    done: state => practiceData(state.plan?.manifest?.config?.dataset_id, state.plan?.manifest?.config?.dataset_version) },
  { id: "plan-reading", page: "training", anchor: "plan-summary", kind: "explain", title: "실행 계획 읽는 법",
    body: "실행 전 확인에는 업무 AI, 학습 데이터 건수, 최대 실행 시간 기준 예상 GPU 비용이 나옵니다.\n‘데이터 전송에 동의하고 학습 시작’은 데이터를 GPU로 보내 실제 학습을 시작하는 버튼이며, 누르면 확인 창이 한 번 더 나옵니다. 연습 데이터는 GPU 전송이 허용되지 않아 실제로 실행되지 않습니다." },
  { id: "jobs-history", page: "jobs", anchor: "jobs-list", kind: "explain", title: "실행 관리와 기록 비교",
    body: "실제 학습을 시작하면 이곳에서 상태, 학습 로그, 실제 train loss 진행과 평가 결과를 봅니다. 앱을 다시 열었을 때는 ‘연결 끊긴 작업 확인’과 ‘재연결 · 결과 회수’로 결과를 가져옵니다.\n왼쪽 메뉴의 ‘기록 비교’에서는 공용 MLflow에 저장된 두 실행의 설정과 지표를 나란히 비교합니다." },
  // Explain-only tour of the project screens (Task 10, D16): after the practice, before the end; no practice, no call.
  { id: "project-start", page: "overview", anchor: "project-start", kind: "explain", title: "빈 프로젝트에서 시작하기",
    body: "예제 없이도 이름과 목적만으로 새 프로젝트를 만들 수 있습니다. 프로젝트에 데이터·레시피·학습 그래프를 연결해 여러 학습을 이어서 실행합니다.\n예제 시작점은 선택 사항입니다." },
  { id: "executor-profiles", page: "profiles", anchor: "executor-profiles", kind: "explain", title: "실행 프로필과 컨트롤러",
    body: "배정받은 Pod나 GPU 서버를 실행 프로필로 여러 개 저장하고 ‘자원 확인’으로 GPU와 요금을 읽어 옵니다. 그래프 실행은 백그라운드 컨트롤러가 진행하며, 시작·정지는 직접 누릅니다.\n앱을 닫아도 컨트롤러는 계속 동작합니다." },
  { id: "data-import", page: "data-import", anchor: "data-import", kind: "explain", title: "열을 연결해 데이터 가져오기",
    body: "CSV·JSON·JSONL 파일의 열을 지시문·입력·정답 같은 학습 형식에 연결하고 미리보기로 행 오류를 확인한 뒤 바뀌지 않는 버전으로 등록합니다. 필요하면 학습·검증·시험으로 나눕니다.\n연습 데이터(tutorial_sample)는 이 경로로 등록할 수 없습니다." },
  { id: "graph-editor", page: "graph", anchor: "graph-editor", kind: "explain", title: "학습 그래프 만들기",
    body: "단계 카드를 직렬(다음 단계)이나 병렬(나란히)로 추가하고 입력을 연결합니다. 미리보기는 카드와 같은 내용으로 그려집니다.\n계획은 기본이 모의 실행이며, 실제 실행은 실행 승인과 데이터 전송 승인을 따로 받습니다." },
  { id: "graph-runs", page: "runs", anchor: "graph-runs", kind: "explain", title: "그래프 실행 따라가기",
    body: "등록한 실행의 단계별 상태, 대기 이유, 기록 저장 상태와 선택 결과를 봅니다. 화면이 보이는 동안만 상태를 다시 읽습니다.\n‘운영자 확인 필요’는 원격 서버나 Pod에서 실행이 멈춘 것을 직접 확인한 뒤에만 ‘실행 잠금 해제’로 풀 수 있습니다." },
  { id: "finish", page: "overview", anchor: "overview-setup", kind: "explain", title: "튜토리얼 완료",
    body: "실제 학습은 두 방법으로 진행합니다. 프로젝트·그래프는 ① 실행 프로필에 배정받은 Pod나 GPU 서버 저장 ② 프로젝트를 만들어 데이터를 가져오고 레시피 준비 ③ 학습 그래프를 저장하고 모의 실행부터 계획 확인 순서입니다. 단일 학습은 ① 연결 설정에서 배정받은 RunPod 또는 GPU 서버 연결 ② 실제 데이터 등록과 GPU 전송 허용 선택 ③ 학습 설정과 실행 계획 확인 순서입니다.\n등록한 tutorial_sample은 연습용으로 남으며 실제 학습에는 쓸 수 없습니다. 튜토리얼은 왼쪽 메뉴의 ‘튜토리얼’에서 다시 볼 수 있습니다.",
    cta: { label: "연결 설정으로 이동", async run(context) { await context.finish(); await context.navigate("connections"); } } },
];
