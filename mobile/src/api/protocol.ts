// Message shapes of Jarvis's phone server (src/remote/phone_server.py). Keep the two in sync.

export const PROTOCOL_VERSION = 1;

/** What the pairing QR code in Jarvis's Settings › Phone app contains. */
export type PairingQr = { v: number; app: 'jarvis'; host: string; port: number; code: string; pc: string };

export type Pairing = { host: string; port: number; deviceId: string; token: string; pc: string };

export type Phase =
  | 'stopped' | 'loading' | 'idle' | 'paused' | 'listening' | 'transcribing' | 'thinking'
  | 'executing' | 'approval' | 'speaking' | 'error';

export type Status = {
  running: boolean; phase: Phase; busy: boolean; muted: boolean; active_task_id: number | null;
  queued: number; pending_approval: string | null; model?: string; voice: boolean; dry_run: boolean; pc: string;
};

export type ApprovalRow = { k: string; v: string; sub?: string };

export type ApprovalRequest = {
  type: 'approval_request'; id: number; tool: string; title: string; kind_label?: string; spoken?: string;
  rows: ApprovalRow[]; timeout_s: number; deadline?: number; source?: string;
};

export type Task = {
  id: number; objective: string; status: string; project?: string | null; summary?: string | null;
  blocked_reason?: string | null; created_at?: number; updated_at?: number;
};

export type ToolRun = {
  id: number; tool: string; status: string; decision?: string; result?: string; error?: string;
  duration_ms?: number; started_at: number;
};

export type Message = { id: number; role: 'user' | 'assistant' | 'error' | 'system'; text: string; source?: string; created_at: number };

/** Controller events mirrored by the server (subset of the desktop UI's events). */
export type JarvisEvent =
  | { type: 'phase'; phase: Phase; tool?: string; purpose?: string; title?: string; mode?: string }
  | { type: 'transcript'; text: string; source?: string; ref?: string | null }
  | { type: 'reply'; text: string; source?: string; ref?: string | null; greeting?: boolean }
  | { type: 'error'; stage?: string; detail: string; source?: string; ref?: string | null }
  | { type: 'cancelled'; detail?: string; ref?: string | null }
  | { type: 'info'; detail: string }
  | ApprovalRequest
  | { type: 'approval_resolved'; id: number; tool?: string; decision: 'allow' | 'deny' | 'timeout' | 'cancelled'; via?: string }
  | { type: 'tool_call'; call_id: string; tool: string; purpose?: string; risk?: string }
  | { type: 'tool_result'; call_id: string; tool: string; ok: boolean; status: string; content?: string; ms?: number }
  | { type: 'task_update'; task: Task }
  | { type: 'task_progress'; task_id: number; message: string }
  | { type: 'ready'; model?: string; voice?: boolean; dry_run?: boolean }
  | { type: 'stopped'; code?: number }
  | { type: 'mute'; muted: boolean }
  | { type: 'timing'; total_ms?: number; ref?: string | null };

export type Welcome = {
  type: 'welcome'; version: number; device: { id: string; name: string }; status: Status;
  active_task: Task | null; pending_approval: ApprovalRequest | null;
};

export type Ack = { type: 'ack'; id: string | null; ok: boolean; result?: unknown; error?: string };

export type ServerMessage = Welcome | Ack | { type: 'event'; event: JarvisEvent };

/** Requests the app sends (the server answers each with an ack carrying the same id). */
export type Request =
  | { type: 'ping' }
  | { type: 'command'; text: string }
  | { type: 'voice'; mime: string; data: string }
  | { type: 'approve'; request_id: number; allowed: boolean }
  | { type: 'cancel' }
  | { type: 'mute'; muted: boolean }
  | { type: 'start' }
  | { type: 'stop' }
  | { type: 'status' }
  | { type: 'screenshot' }
  | { type: 'history'; limit?: number }
  | { type: 'tasks' }
  | { type: 'activity' }
  | { type: 'task'; task_id: number; action: 'pause' | 'cancel' | 'resume' | 'retry'; answer?: string };

/** WebSocket close codes the server uses. */
export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_REVOKED = 4403;

export function parseQr(data: string): PairingQr | null {
  try {
    const q = JSON.parse(data);
    if (q && q.app === 'jarvis' && typeof q.host === 'string' && Number.isInteger(q.port) && /^\d{6}$/.test(q.code)) {
      return q as PairingQr;
    }
  } catch {
    // not a Jarvis pairing code
  }
  return null;
}
