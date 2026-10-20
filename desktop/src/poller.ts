// The read-only poller of D10. Pure: timers are injectable, so Node tests drive it with a fake clock.
// It never overlaps calls; the next delay is max(base, 2 × the last call's duration); each failure doubles the
// delay up to `maxMs` and a success resets it; an inactive page skips the call and checks again after `baseMs`.

export interface PollerOptions {
  baseMs: number; maxMs?: number;                                // maxMs default 60000
  tick(): Promise<"continue" | "stop">;
  active(): boolean;                                             // page shown, document visible, not busy
  timers?: { set(fn: () => void, ms: number): unknown; clear(handle: unknown): void; now(): number };
}
export interface Poller { start(): void; stop(): void; poke(): void; readonly running: boolean; readonly failures: number }

const browserTimers = {
  set: (fn: () => void, ms: number): unknown => setTimeout(fn, ms),
  clear: (handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

export function createPoller(options: PollerOptions): Poller {
  const timers = options.timers ?? browserTimers;
  const maxMs = options.maxMs ?? 60000;
  let running = false, inFlight = false, again = false, failures = 0;
  let handle: unknown = null;

  function schedule(ms: number) {
    if (handle !== null) timers.clear(handle);
    handle = timers.set(fire, Math.min(maxMs, Math.max(0, ms)));
  }

  function fire() {
    handle = null;
    if (!running) return;
    if (inFlight) { again = true; return; }
    if (!options.active()) { schedule(options.baseMs); return; }
    void run();
  }

  async function run() {
    inFlight = true; again = false;
    const started = timers.now();
    let outcome: "continue" | "stop" | "failed";
    try { outcome = await options.tick(); }
    catch { outcome = "failed"; }
    inFlight = false;
    const duration = Math.max(0, timers.now() - started);
    if (outcome === "failed") failures++;
    else failures = 0;
    if (!running) return;
    if (outcome === "stop") { stop(); return; }
    if (again) { schedule(0); return; }
    schedule(outcome === "failed" ? Math.max(options.baseMs * 2 ** failures, 2 * duration) : Math.max(options.baseMs, 2 * duration));
  }

  function stop() {
    running = false; again = false;
    if (handle !== null) { timers.clear(handle); handle = null; }
  }

  return {
    start() {
      if (running) return;
      running = true; failures = 0;
      // A call still in flight from before a stop schedules the next one when it ends.
      if (inFlight) again = false;
      else schedule(options.baseMs);
    },
    stop,
    poke() {
      if (!running) return;
      if (inFlight) { again = true; return; }
      schedule(0);
    },
    get running() { return running; },
    get failures() { return failures; },
  };
}

/** One call in flight per page: work submitted while another runs waits for it (a failure does not block the queue). */
export function createQueue(): { run<T>(work: () => Promise<T>): Promise<T> } {
  let tail: Promise<unknown> = Promise.resolve();
  return {
    run<T>(work: () => Promise<T>): Promise<T> {
      const result = tail.then(() => work());
      tail = result.then(() => undefined, () => undefined);
      return result;
    },
  };
}
