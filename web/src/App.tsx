import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  ImagePlus,
  MessageCircle,
  Plus,
  ShieldCheck,
  Square,
  ThumbsDown,
  X,
  ZoomIn,
} from "lucide-react";
import {
  active,
  api,
  explain,
  mediaUrl,
  mergeRun,
  messageId,
  setCSRF,
  upload,
  type Me,
  type Product,
  type Run,
  type Session,
  type Source,
} from "./api";

type Attachment = { upload_id: string; name: string };
type Modal =
  | { kind: "source"; id: string; data?: Source; error?: string }
  | { kind: "upload"; id: string }
  | { kind: "feedback"; run: Run };
let authentication: Promise<Me> | undefined;
function authenticate() {
  return (authentication ??= api<Me>("/me")
    .catch(() => api<Me>("/auth/demo", "POST"))
    .then((me) => {
      setCSRF(me.csrf);
      return me;
    }));
}
function Picture({
  id,
  kind = "assets",
  alt,
  onOpen,
}: {
  id: string;
  kind?: "assets" | "uploads";
  alt: string;
  onOpen?: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const url = mediaUrl(kind, id);
  useEffect(() => setFailed(false), [id]);
  if (!url || failed)
    return (
      <p className="image-error" role="status">
        图片暂不可用，请查看来源状态。
      </p>
    );
  if (!onOpen)
    return (
      <div className="picture static">
        <img src={url} alt={alt} onError={() => setFailed(true)} />
      </div>
    );
  return (
    <button
      className="picture"
      onClick={onOpen}
      type="button"
      aria-label={alt + "，放大查看"}
    >
      <img src={url} alt={alt} onError={() => setFailed(true)} />
      <span className="zoom">
        <ZoomIn size={16} />
        放大
      </span>
    </button>
  );
}

export default function App() {
  const [me, setMe] = useState<Me>();
  const [products, setProducts] = useState<Product[]>([]);
  const [session, setSession] = useState<Session>();
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [changing, setChanging] = useState(false);
  const transition = useRef<Promise<boolean> | undefined>(undefined);
  const sendingRef = useRef(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [progress, setProgress] = useState<string[]>([]);
  const [modal, setModal] = useState<Modal | null>(null);
  const [feedbackReason, setFeedbackReason] = useState("answer");
  const [feedbackNote, setFeedbackNote] = useState("");
  const [zoom, setZoom] = useState(1);
  const [reply, setReply] = useState<{ id: string; label: string }>();
  const sessionRef = useRef<Session | undefined>(undefined);
  const stream = useRef<EventSource | null>(null);
  const lastSeq = useRef(0);
  const trackedRun = useRef("");
  const epoch = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scrollArea = useRef<HTMLElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const pending = useRef<
    | {
        client_message_id: string;
        expected_session_revision: number;
        text: string;
        attachment_ids: string[];
        reply_to_step_id: string | null;
      }
    | undefined
  >(undefined);
  const busy = !!session?.active_run;
  const current = products.find((p) => p.product_id === session?.product);
  function putSession(value: Session) {
    if (
      sessionRef.current?.id === value.id &&
      sessionRef.current.revision > value.revision
    )
      return;
    sessionRef.current = value;
    setSession(value);
  }
  function notify(value: string) {
    setToast(value);
    window.setTimeout(() => setToast(""), 3500);
  }
  async function refresh(id: string) {
    const value = await api<Session>("/sessions/" + id);
    if (sessionRef.current?.id === id) putSession(value);
    return value;
  }
  function watch(run: Run) {
    stream.current?.close();
    trackedRun.current = run.run_id;
    lastSeq.current = 0;
    setProgress(["请求已接收"]);
    if (!active(run.status)) {
      void refresh(run.session_id);
      return;
    }
    const capturedEpoch = epoch.current;
    const source = new EventSource("/v1/runs/" + run.run_id + "/events");
    stream.current = source;
    source.addEventListener("progress", (event) => {
      if (epoch.current !== capturedEpoch || trackedRun.current !== run.run_id)
        return;
      const item = JSON.parse((event as MessageEvent).data) as {
        seq: number;
        label: string;
      };
      if (item.seq <= lastSeq.current) return;
      lastSeq.current = item.seq;
      setProgress((old) =>
        [...old.filter((label) => label !== item.label), item.label].slice(-4),
      );
    });
    source.addEventListener("finished", () => {
      source.close();
      if (
        epoch.current === capturedEpoch &&
        trackedRun.current === run.run_id
      ) {
        setProgress([]);
        void refresh(run.session_id).catch((e) => setError(explain(e)));
      }
    });
    source.onerror = () => {
      if (
        source.readyState === EventSource.CLOSED &&
        epoch.current === capturedEpoch
      )
        void refresh(run.session_id).catch(() => {});
    };
  }
  useEffect(() => {
    let disposed = false;
    void (async () => {
      try {
        const identity = await authenticate();
        if (disposed) return;
        setMe(identity);
        const catalog = await api<{ items: Product[] }>("/catalog/purchases");
        if (disposed) return;
        setProducts(catalog.items);
        const key = "sg.session." + identity.user_id;
        const saved = localStorage.getItem(key);
        let value: Session;
        try {
          value = saved
            ? await api<Session>("/sessions/" + saved)
            : await api<Session>("/sessions", "POST");
        } catch {
          value = await api<Session>("/sessions", "POST");
        }
        if (disposed) return;
        value.messages ??= [];
        putSession(value);
        localStorage.setItem(key, value.id);
        const running = value.messages.find(
          (r) => r.run_id === value.active_run,
        );
        if (running) watch(running);
      } catch (e) {
        if (!disposed) setError(explain(e));
      }
    })();
    return () => {
      disposed = true;
      stream.current?.close();
    };
  }, []);
  useEffect(() => {
    scrollArea.current?.scrollTo({
      top: scrollArea.current.scrollHeight,
      behavior: "smooth",
    });
  }, [session?.messages.length, session?.messages.at(-1)?.status, progress]);
  useEffect(() => {
    if (modal && !dialog.current?.open) dialog.current?.showModal();
    if (!modal && dialog.current?.open) dialog.current.close();
  }, [modal]);
  useEffect(() => {
    if (!session?.active_run) return;
    const id = session.id;
    const timer = setInterval(() => {
      void api<Run>("/runs/" + session.active_run)
        .then((run) => {
          if (!active(run.status) && sessionRef.current?.id === id) {
            stream.current?.close();
            setProgress([]);
            void refresh(id);
          }
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [session?.active_run]);
  function beginTransition() {
    let release!: (committed: boolean) => void;
    transition.current = new Promise<boolean>((resolve) => {
      release = resolve;
    });
    setChanging(true);
    return (committed: boolean) => {
      transition.current = undefined;
      setChanging(false);
      release(committed);
    };
  }
  async function choose(productId: string) {
    const value = sessionRef.current;
    const product = products.find((p) => p.product_id === productId);
    if (
      !value ||
      !product ||
      busy ||
      transition.current ||
      sendingRef.current ||
      uploading
    )
      return;
    const done = beginTransition();
    let committed = false;
    try {
      setError("");
      const updated = await api<Session>(
        "/sessions/" + value.id + "/product",
        "POST",
        {
          product_id: product.product_id,
          variant_id: product.variant_id,
          expected_revision: value.revision,
        },
      );
      epoch.current++;
      stream.current?.close();
      pending.current = undefined;
      setAttachments([]);
      setReply(undefined);
      putSession({ ...updated, messages: value.messages });
      committed = true;
      const last = value.messages.at(-1);
      if (!value.product && last?.result?.status === "needs_clarification")
        setText(last.question);
      notify("已选择 " + product.model);
      composer.current?.focus();
    } catch (e) {
      setError(explain(e));
      await refresh(value.id);
    } finally {
      done(committed);
    }
  }
  async function newConversation() {
    if (!me || busy || transition.current || sendingRef.current || uploading)
      return;
    const done = beginTransition();
    let committed = false;
    try {
      const value = await api<Session>("/sessions", "POST");
      value.messages = [];
      epoch.current++;
      stream.current?.close();
      putSession(value);
      committed = true;
      localStorage.setItem("sg.session." + me.user_id, value.id);
      setText("");
      setAttachments([]);
      setReply(undefined);
      pending.current = undefined;
      setError("");
    } catch (e) {
      setError(explain(e));
    } finally {
      done(committed);
    }
  }
  async function send(
    override?: string,
    attachmentOverride?: string[],
    replyOverride?: string | null,
  ) {
    const question = (override ?? text).trim();
    const switching = transition.current;
    if (switching && !(await switching)) return;
    const value = sessionRef.current;
    if (
      !value ||
      !question ||
      sendingRef.current ||
      value.active_run ||
      uploading
    )
      return;
    const payload = pending.current ?? {
      client_message_id: messageId(),
      expected_session_revision: value.revision,
      text: question,
      attachment_ids:
        attachmentOverride ??
        (switching ? [] : attachments.map((a) => a.upload_id)),
      reply_to_step_id:
        replyOverride !== undefined
          ? replyOverride
          : switching
            ? null
            : (reply?.id ?? null),
    };
    pending.current = payload;
    sendingRef.current = true;
    setSending(true);
    setError("");
    try {
      const run = await api<Run>(
        "/sessions/" + value.id + "/messages",
        "POST",
        payload,
      );
      pending.current = undefined;
      setText("");
      setAttachments([]);
      setReply(undefined);
      putSession({
        ...value,
        revision: run.revision,
        active_run: active(run.status) ? run.run_id : null,
        messages: mergeRun(value.messages, run),
      });
      watch(run);
    } catch (e) {
      setError(explain(e));
      if ((e as { status?: number }).status === 409) {
        pending.current = undefined;
        await refresh(value.id);
      }
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }
  async function cancel() {
    const id = sessionRef.current?.active_run;
    if (!id) return;
    try {
      await api("/runs/" + id + "/cancel", "POST");
      notify("已请求取消");
      await refresh(sessionRef.current!.id);
    } catch (e) {
      setError(explain(e));
    }
  }
  async function files(selected: FileList | null) {
    const value = sessionRef.current;
    if (!value || !selected) return;
    if (selected.length + attachments.length > 4) {
      setError("每条消息最多附 4 张图片。");
      return;
    }
    pending.current = undefined;
    setUploading(true);
    setError("");
    const captured = epoch.current;
    try {
      for (const file of Array.from(selected)) {
        if (
          !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
          file.size > 20 * 1024 * 1024
        )
          throw new Error("UPLOAD_FORMAT");
        const result = await upload(file, value.id);
        if (epoch.current === captured)
          setAttachments((old) => [
            ...old,
            { upload_id: result.upload_id, name: file.name },
          ]);
      }
    } catch (e) {
      setError(explain(e));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  async function source(id: string) {
    setZoom(1);
    setModal({ kind: "source", id });
    try {
      const data = await api<Source>("/sources/" + id);
      setModal((old) =>
        old?.kind === "source" && old.id === id ? { ...old, data } : old,
      );
    } catch (e) {
      setModal((old) =>
        old?.kind === "source" && old.id === id
          ? { ...old, error: explain(e) }
          : old,
      );
    }
  }
  async function confirm(run: Run) {
    const result = run.result;
    if (!result?.confirmation_id || !sessionRef.current) return;
    try {
      setError("");
      const resumed = await api<Run>(
        "/runs/" + run.run_id + "/confirm",
        "POST",
        {
          confirmation_id: result.confirmation_id,
          arguments_hash: result.arguments_hash,
          expected_session_revision: run.revision,
        },
      );
      putSession({
        ...sessionRef.current,
        active_run: resumed.run_id,
        messages: mergeRun(sessionRef.current.messages, resumed),
      });
      watch(resumed);
    } catch (e) {
      setError(explain(e));
    }
  }
  async function feedback(event: FormEvent) {
    event.preventDefault();
    if (modal?.kind !== "feedback") return;
    try {
      await api("/feedback", "POST", {
        run_id: modal.run.run_id,
        reason: feedbackReason,
        note: feedbackNote,
      });
      setModal(null);
      setFeedbackNote("");
      notify("反馈已保存");
    } catch (e) {
      setError(explain(e));
    }
  }
  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-icon">
            <BookOpen size={24} />
          </span>
          <div>
            <strong>ShopGuide</strong>
            <small>商品使用助手</small>
          </div>
        </div>
        <button
          className="new-chat"
          onClick={() => void newConversation()}
          disabled={busy || changing || !session}
        >
          <Plus size={18} />
          新建会话
        </button>
        <div className="section-label">
          我的商品 <span>{products.length}</span>
        </div>
        <div className="product-list">
          {products.map((p, i) => (
            <button
              key={p.product_id}
              data-testid={"product-" + p.model}
              className={
                "product " +
                (p.product_id === session?.product ? "selected" : "")
              }
              onClick={() => void choose(p.product_id)}
              disabled={busy || changing || !session}
              aria-pressed={p.product_id === session?.product}
            >
              <span className="product-number">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span>
                <small>{p.brand}</small>
                <strong>{p.model}</strong>
                <span>{p.category}</span>
              </span>
              {p.product_id === session?.product && <Check size={17} />}
            </button>
          ))}
        </div>
        <div className="rail-note">
          <ShieldCheck size={19} />
          <p>
            操作有依据，配图可溯源。
            <br />
            切换型号后重新查阅适用资料。
          </p>
        </div>
        <div className="profile">
          <span className="avatar">{me ? "演" : "·"}</span>
          <div>
            {me?.display_name ?? "正在连接"}
            <small>
              {me?.mode === "sample" ? "示例答复模式" : "DeepSeek 辅助分析"}
            </small>
          </div>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">使用指导</span>
            <h1>{current?.model ?? "选择商品，开始咨询"}</h1>
          </div>
          {me?.demo && <span className="demo-badge">示例商品与资料</span>}
          <button
            className="service-button"
            disabled={!current || busy || changing}
            onClick={() => void send("请为这个商品准备模拟售后工单。")}
          >
            模拟售后
            <ChevronRight size={16} />
          </button>
        </header>
        <div className="mobile-picker">
          <label htmlFor="product-select">当前商品</label>
          <select
            id="product-select"
            value={session?.product ?? ""}
            disabled={busy || changing || !session}
            onChange={(e) => void choose(e.target.value)}
          >
            <option value="" disabled>
              请选择型号
            </option>
            {products.map((p) => (
              <option key={p.product_id} value={p.product_id}>
                {p.model}
              </option>
            ))}
          </select>
        </div>
        <section
          ref={scrollArea}
          className="conversation"
          aria-label="聊天记录"
        >
          <div className="conversation-inner">
            {!session?.messages.length && (
              <div className="welcome">
                <div className="welcome-icon">
                  <MessageCircle size={28} />
                </div>
                <h2>你想了解哪项操作？</h2>
                <p>
                  选择你的商品，询问操作方法、按钮位置或注意事项。答复中的原图可以放大并查看来源。
                </p>
                <div className="suggestions">
                  {[
                    "怎样启动这个商品？",
                    "按钮在哪里？",
                    "使用时有哪些注意事项？",
                  ].map((q) => (
                    <button
                      key={q}
                      disabled={!session || busy || changing}
                      onClick={() => void send(q)}
                    >
                      {q}
                      <ChevronRight size={15} />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {session?.messages.map((run) => {
              const result = run.result;
              const answer = result?.answer;
              const product = products.find(
                (p) => p.product_id === run.product_id,
              );
              return (
                <article
                  className="exchange"
                  key={run.run_id}
                  data-testid="exchange"
                >
                  <div className="user-row">
                    <div className="user-message">
                      {product && <small>{product.model}</small>}
                      <p>{run.question}</p>
                      {run.attachment_ids?.map((id) => (
                        <Picture
                          key={id}
                          id={id}
                          kind="uploads"
                          alt="你上传的图片"
                          onOpen={() => setModal({ kind: "upload", id })}
                        />
                      ))}
                    </div>
                    <span className="user-avatar">我</span>
                  </div>
                  <div className="assistant-row">
                    <span className="assistant-avatar">
                      <BookOpen size={19} />
                    </span>
                    <div className="assistant-content">
                      <div className="assistant-heading">
                        ShopGuide
                        {answer && (
                          <span>
                            <ShieldCheck size={13} />
                            {answer.verification.semantic === "supported"
                              ? "已核对依据"
                              : "示例答复 · 来源可查"}
                          </span>
                        )}
                      </div>
                      {active(run.status) && (
                        <div className="progress" role="status">
                          <span className="pulse" />
                          {progress.at(-1) ?? "正在处理…"}
                          <small>{progress.slice(0, -1).join(" · ")}</small>
                        </div>
                      )}
                      {result?.status === "needs_clarification" && (
                        <p className="clarify">{result.question}</p>
                      )}
                      {result?.status === "source_unavailable" && (
                        <p className="notice" role="alert">
                          {result.message}
                        </p>
                      )}
                      {answer && (
                        <div className="answer">
                          {(!answer.steps.length ||
                            answer.summary !== answer.steps[0].text) && (
                            <p>{answer.summary}</p>
                          )}
                          {answer.prerequisites?.length > 0 && (
                            <div className="notice">
                              {answer.prerequisites.map((p, i) => (
                                <p key={i}>{p}</p>
                              ))}
                            </div>
                          )}
                          {answer.steps.map((step, index) => (
                            <section className="step-card" key={step.step_id}>
                              <div className="step-title">
                                <span>{index + 1}</span>
                                <h3>
                                  {step.title === "Source excerpt"
                                    ? "资料说明"
                                    : step.title}
                                </h3>
                              </div>
                              <p>{step.text}</p>
                              <div className="step-images">
                                {step.display_asset_ids.map((aid) => {
                                  const citation =
                                    result?.sources?.find((s) =>
                                      s.asset_ids.includes(aid),
                                    )?.evidence_id ?? step.evidence_ids[0];
                                  return (
                                    <figure key={aid}>
                                      <Picture
                                        id={aid}
                                        alt={`步骤 ${index + 1} 的来源原图`}
                                        onOpen={() => void source(citation)}
                                      />
                                      <figcaption>
                                        <BookOpen size={14} />
                                        说明书原图
                                        <button
                                          onClick={() => void source(citation)}
                                        >
                                          查看来源
                                          <ChevronRight size={14} />
                                        </button>
                                      </figcaption>
                                    </figure>
                                  );
                                })}
                              </div>
                              <div className="step-actions">
                                <button
                                  onClick={() => {
                                    setReply({
                                      id: step.step_id,
                                      label: `追问第 ${index + 1} 步`,
                                    });
                                    setText(
                                      `第 ${index + 1} 步提到的操作怎么完成？`,
                                    );
                                    composer.current?.focus();
                                  }}
                                  disabled={
                                    busy || run.task_id !== session?.task
                                  }
                                >
                                  追问这一步
                                </button>
                                {step.evidence_ids.map((id, i) => (
                                  <button
                                    key={id}
                                    className="citation"
                                    onClick={() => void source(id)}
                                  >
                                    来源 {i + 1}
                                  </button>
                                ))}
                              </div>
                            </section>
                          ))}
                          {answer.followup_question && (
                            <p className="notice">{answer.followup_question}</p>
                          )}
                          {answer.unresolved_items?.map((value, i) => (
                            <p className="notice" key={i}>
                              {value}
                            </p>
                          ))}
                          <button
                            className="feedback-link"
                            onClick={() => {
                              setModal({ kind: "feedback", run });
                              setFeedbackReason("answer");
                            }}
                          >
                            <ThumbsDown size={15} />
                            反馈问题
                          </button>
                        </div>
                      )}
                      {result?.status === "waiting_confirmation" && (
                        <div className="confirmation">
                          <span className="eyebrow">待你确认</span>
                          <h3>创建模拟售后申请</h3>
                          <p>{result.summary?.summary}</p>
                          <p className="subtle">
                            仅登记本地模拟工单，不触发真实售后操作。
                          </p>
                          <div>
                            <button
                              className="primary"
                              onClick={() => void confirm(run)}
                              disabled={
                                busy ||
                                run.task_id !== session?.task ||
                                run.revision !== session?.revision
                              }
                            >
                              确认创建
                            </button>
                            <button
                              onClick={() =>
                                void api(
                                  "/runs/" + run.run_id + "/cancel",
                                  "POST",
                                )
                                  .then(() => refresh(run.session_id))
                                  .catch((e) => setError(explain(e)))
                              }
                            >
                              取消申请
                            </button>
                          </div>
                        </div>
                      )}
                      {result?.service_query && (
                        <div className="notice">
                          <p>{result.service_query.message}</p>
                          {result.service_query.tickets.map((ticket) => (
                            <div key={ticket.ticket_id}>
                              <strong>{ticket.state_label}</strong>
                              <p>{ticket.ticket_id}</p>
                              <p>{ticket.summary}</p>
                            </div>
                          ))}
                          {result.service_query.has_more && <p>仅显示 20 条记录，请提供工单编号查询。</p>}
                        </div>
                      )}
                      {result?.service_request && (
                        <div className="success">
                          <Check size={18} />
                          <div>
                            <strong>模拟工单已创建</strong>
                            <small>{result.service_request.ticket_id}</small>
                            <button disabled={busy || run.task_id !== session?.task}
                              onClick={() => {
                                setText(`查询工单 ${result.service_request!.ticket_id}`);
                                composer.current?.focus();
                              }}>查询此工单</button>
                          </div>
                        </div>
                      )}
                      {run.status === "CANCELLED" && (
                        <p className="subtle">
                          已取消处理。已完成的模拟操作仍保留。
                        </p>
                      )}
                      {result?.status === "acknowledged" && (
                        <p className="notice">{result.message}</p>
                      )}
                      {result?.status === "abstained" && (
                        <p className="notice">
                          {result.message || "现有资料不足以支持可靠答复，暂时无法给出结论。"}
                        </p>
                      )}
                      {result?.status === "handoff_offered" && (
                        <p className="notice">
                          需要进一步协助，可以准备模拟售后申请。
                        </p>
                      )}
                      {run.status === "FAILED" && (
                        <div className="notice">
                          <p>
                            {result?.code === "OUTCOME_UNKNOWN"
                              ? "操作结果尚未确认，请先核对记录。"
                              : "这次处理未完成，你可以保留问题后重试。"}
                          </p>
                          {result?.code !== "OUTCOME_UNKNOWN" &&
                            !result?.service_request && (
                              <button
                                disabled={busy}
                                onClick={() => {
                                  pending.current = undefined;
                                  void send(
                                    run.question,
                                    run.attachment_ids,
                                    run.reply_to_step_id,
                                  );
                                }}
                              >
                                重试问题
                              </button>
                            )}
                        </div>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
            <div ref={bottom} />
          </div>
        </section>
        <footer className="composer-area">
          <div className="composer-inner">
            {error && (
              <div className="error-banner" role="alert">
                {error}
                <button aria-label="关闭提示" onClick={() => setError("")}>
                  <X size={16} />
                </button>
              </div>
            )}
            {reply && (
              <div className="reply-tag">
                {reply.label}
                <button
                  aria-label="取消步骤引用"
                  onClick={() => setReply(undefined)}
                >
                  <X size={14} />
                </button>
              </div>
            )}
            {attachments.length > 0 && (
              <div className="attachments">
                {attachments.map((a) => (
                  <div key={a.upload_id}>
                    <img src={mediaUrl("uploads", a.upload_id)} alt={a.name} />
                    <span>{a.name}</span>
                    <button
                      aria-label={"移除 " + a.name}
                      onClick={() =>
                        setAttachments((old) =>
                          old.filter((x) => x.upload_id !== a.upload_id),
                        )
                      }
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <textarea
                ref={composer}
                aria-label="输入问题"
                value={text}
                maxLength={8000}
                placeholder={
                  current
                    ? `询问 ${current.model} 的使用方法…`
                    : "描述问题，或先选择商品型号…"
                }
                onChange={(e) => {
                  setText(e.target.value);
                  pending.current = undefined;
                }}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <div className="composer-controls">
                <button
                  type="button"
                  className="attach-button"
                  disabled={!session || uploading || busy || changing}
                  onClick={() => fileInput.current?.click()}
                >
                  <ImagePlus size={18} />
                  {uploading ? "上传中…" : "添加图片"}
                </button>
                <input
                  ref={fileInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  multiple
                  hidden
                  onChange={(e) => void files(e.target.files)}
                />
                <span className="keyboard-hint">
                  Enter 发送 · Shift + Enter 换行
                </span>
                {busy ? (
                  <button
                    type="button"
                    className="cancel-button"
                    onClick={() => void cancel()}
                  >
                    <Square size={15} />
                    取消处理
                  </button>
                ) : (
                  <button
                    className="send"
                    type="submit"
                    aria-label="发送问题"
                    disabled={!text.trim() || !session || sending || uploading}
                  >
                    <ArrowUp size={20} />
                  </button>
                )}
              </div>
            </form>
            <p className="composer-note">
              {me?.mode === "sample"
                ? "示例模式：答复用于检查功能与引用链路。"
                : "图片将交由 DeepSeek 分析，仅用于当前会话。"}{" "}
              请以适用型号的真实资料为准。
            </p>
          </div>
        </footer>
      </main>
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
        </div>
      )}
      <dialog
        ref={dialog}
        className="dialog"
        onCancel={() => setModal(null)}
        onClick={(e) => {
          if (e.target === e.currentTarget) setModal(null);
        }}
        aria-label={modal?.kind === "feedback" ? "反馈问题" : "查看原图来源"}
      >
        <div className="dialog-inner">
          <header>
            <div>
              <span className="eyebrow">
                {modal?.kind === "feedback" ? "帮助改进" : "原始资料"}
              </span>
              <h2>
                {modal?.kind === "feedback"
                  ? "反馈问题"
                  : modal?.kind === "upload"
                    ? "你上传的图片"
                    : (modal?.data?.product ?? "查看来源")}
              </h2>
            </div>
            <button aria-label="关闭弹窗" onClick={() => setModal(null)}>
              <X size={22} />
            </button>
          </header>
          {modal?.kind === "source" && (
            <>
              {modal.error ? (
                <p role="alert" className="notice">
                  {modal.error}
                </p>
              ) : !modal.data ? (
                <p role="status">正在读取来源…</p>
              ) : (
                <>
                  <div className="source-meta">
                    文档第 {modal.data.page_index_0based + 1} 页 · 版本标识{" "}
                    {modal.data.version_label}
                  </div>
                  {modal.data.full_page_asset_id && (
                    <>
                      <div className="source-zoom">
                        <button
                          onClick={() => setZoom((z) => Math.max(1, z - 0.5))}
                          disabled={zoom <= 1}
                        >
                          缩小
                        </button>
                        <button onClick={() => setZoom(1)}>适应宽度</button>
                        <button
                          onClick={() => setZoom((z) => Math.min(3, z + 0.5))}
                          disabled={zoom >= 3}
                        >
                          放大
                        </button>
                      </div>
                      <div className="source-scroll">
                        <div style={{ width: zoom * 100 + "%" }}>
                          <Picture
                            id={modal.data.full_page_asset_id}
                            alt="说明书整页"
                          />
                        </div>
                      </div>
                    </>
                  )}
                  <h3>来源文字</h3>
                  <pre>{modal.data.text}</pre>
                </>
              )}
            </>
          )}
          {modal?.kind === "upload" && (
            <Picture id={modal.id} kind="uploads" alt="你上传的原图" />
          )}
          {modal?.kind === "feedback" && (
            <form onSubmit={(e) => void feedback(e)}>
              <label>
                问题类型
                <select
                  value={feedbackReason}
                  onChange={(e) => setFeedbackReason(e.target.value)}
                >
                  <option value="answer">答非所问</option>
                  <option value="model">型号不符</option>
                  <option value="image">配图错误</option>
                  <option value="steps">步骤不清晰</option>
                </select>
              </label>
              <label>
                补充说明（可选）
                <textarea
                  value={feedbackNote}
                  maxLength={1000}
                  onChange={(e) => setFeedbackNote(e.target.value)}
                />
              </label>
              <button className="primary">提交反馈</button>
            </form>
          )}
        </div>
      </dialog>
    </div>
  );
}
