export type Product = {
  product_id: string;
  variant_id: string;
  order_item_id: string;
  brand: string;
  model: string;
  category: string;
};
export type Step = {
  step_id: string;
  title: string;
  text: string;
  evidence_ids: string[];
  display_asset_ids: string[];
};
export type Answer = {
  status: string;
  summary: string;
  prerequisites: string[];
  steps: Step[];
  citations: { evidence_id: string }[];
  unresolved_items: string[];
  followup_question?: string | null;
  verification: { semantic: string };
};
export type Result = {
  status: string;
  answer?: Answer;
  question?: string;
  message?: string;
  reason?: string;
  code?: string;
  sources?: {
    evidence_id: string;
    asset_ids: string[];
    page_index_0based: number;
  }[];
  confirmation_id?: string;
  arguments_hash?: string;
  summary?: { reason: string; summary: string };
  service_query?: {
    status: string;
    simulated: boolean;
    message: string;
    has_more: boolean;
    tickets: { ticket_id: string; state: string; state_label: string; summary: string; related_order_item: string }[];
  };
  service_request?: { ticket_id: string; simulated: boolean };
};
export type Run = {
  run_id: string;
  session_id: string;
  revision: number;
  task_id: string;
  question: string;
  status: string;
  result: Result | null;
  attachment_ids: string[];
  product_id: string | null;
  reply_to_step_id: string | null;
};
export type Session = {
  id: string;
  revision: number;
  task: string;
  product: string | null;
  variant: string | null;
  active_run: string | null;
  messages: Run[];
};
export type Me = {
  csrf: string;
  demo: boolean;
  mode: string;
  user_id: string;
  display_name: string;
};
export type Source = {
  evidence_id: string;
  product: string;
  page_index_0based: number;
  page_label: string | null;
  version_label: string;
  text: string;
  full_page_asset_id: string | null;
  asset_ids: string[];
};
let csrf = "";
export class APIError extends Error {
  constructor(
    public code: string,
    public status: number,
  ) {
    super(code);
  }
}
export function setCSRF(value: string) {
  csrf = value;
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/v1" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(method === "GET"
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf }),
    },
    ...(method === "GET" ? {} : { body: JSON.stringify(body ?? {}) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new APIError(data.detail?.code ?? "REQUEST_FAILED", response.status);
  return data;
}
export async function upload(
  file: File,
  session: string,
): Promise<{ upload_id: string; width: number; height: number }> {
  const response = await fetch(
    "/v1/uploads?session_id=" + encodeURIComponent(session),
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": file.type, "X-CSRF-Token": csrf },
      body: file,
    },
  );
  const data = await response.json();
  if (!response.ok)
    throw new APIError(data.detail?.code ?? "UPLOAD_FORMAT", response.status);
  return data;
}
export function mediaUrl(
  kind: "assets" | "uploads",
  id: string,
): string | undefined {
  const prefix = kind === "assets" ? "asset_" : "upload_";
  return id.startsWith(prefix) && /^[a-z][a-z0-9_]{2,95}$/.test(id)
    ? `/v1/${kind}/${id}`
    : undefined;
}
export function mergeRun(runs: Run[], run: Run): Run[] {
  return [...runs.filter((r) => r.run_id !== run.run_id), run].sort(
    (a, b) => a.revision - b.revision,
  );
}
export const active = (status: string) =>
  status === "QUEUED" || status === "RUNNING";
export const messageId = () =>
  "message_" + crypto.randomUUID().replaceAll("-", "");
export function explain(error: unknown): string {
  const code = error instanceof APIError ? error.code : "";
  const messages: Record<string, string> = {
    SESSION_REVISION_CONFLICT: "会话已更新，请重试。",
    SESSION_BUSY: "当前问题尚未处理完，请稍候或取消。",
    RATE_LIMITED: "正在处理的请求较多，请稍后再试。",
    MODEL_OR_QUEUE_UNAVAILABLE: "模型服务暂时不可用，请稍后重试。",
    RESOURCE_UNAVAILABLE: "资料不存在或当前无法访问。",
    AUTH_REQUIRED: "请通过受信任的入口登录。",
    AUTH_EXPIRED: "会话认证已过期，请刷新页面。",
    UPLOAD_SIZE: "图片过大，请使用不超过 20 MB 的图片。",
    UPLOAD_PIXELS: "图片分辨率过高，请压缩后再上传。",
    UPLOAD_FORMAT: "请上传有效的 PNG、JPEG 或 WebP 静态图片。",
    CONFIRMATION_EXPIRED: "确认已过期，请重新准备申请。",
    CONFIRMATION_STALE: "商品或会话已变化，请重新准备申请。",
  };
  return messages[code] ?? "请求未完成，请检查连接后重试。";
}
