"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const DEFAULT_API_BASE = "http://localhost:8000";

type Role = "user" | "assistant" | "system";

type Message = {
  id: string;
  role: Role;
  content: string;
  streaming?: boolean;
};

type WsMode = "chat" | "voice";

type AuthView = "login" | "signup" | "verify";

type NoticeTone = "info" | "success" | "error";

type Notice = {
  tone: NoticeTone;
  message: string;
};

const LOCAL_KEYS = {
  apiBase: "knowledge-ai-api-base",
  token: "knowledge-ai-access-token",
  kbUserId: "knowledge-ai-kb-user-id",
  wsMode: "knowledge-ai-ws-mode",
};

const CHANNELS = ["WhatsApp", "Telegram", "Slack", "Discord", "Web Chat"];

const FEATURE_CARDS = [
  {
    title: "Team collaboration",
    description:
      "Invite operators with Owner/Admin/Member roles and scoped permissions.",
  },
  {
    title: "One-click integrations",
    description:
      "Connect Google, Notion, GitHub, and more without managing OAuth apps.",
  },
  {
    title: "Unified credits",
    description:
      "Route traffic across multiple models with a single credit wallet.",
  },
  {
    title: "Skill security scanning",
    description:
      "Automated AI reviews catch unsafe or malicious skills before install.",
  },
  {
    title: "Autonomous goal tracking",
    description:
      "Heartbeat scheduler keeps your assistant aligned with objectives.",
  },
  {
    title: "Sandboxed execution",
    description:
      "Every run isolates code in secure microVM sandboxes for safety.",
  },
];

const SECURITY_POINTS = [
  "AES-256-GCM encryption for keys and credentials",
  "Encrypted conversation history with per-tenant isolation",
  "Rate limiting and flood protection out of the box",
  "Audit trails for logins, integrations, and billing events",
];

const STEPS = [
  {
    title: "Sign up and configure",
    description: "Name your assistant, select a model, and set guardrails.",
  },
  {
    title: "Connect channels",
    description: "Link your chat apps and web chat in minutes.",
  },
  {
    title: "Go live",
    description: "Launch customer-ready agents with analytics and support tools.",
  },
];

const PRICING = [
  {
    title: "Starter",
    price: "$0",
    description: "Launch fast with a developer-first sandbox.",
    bullets: ["1 workspace", "Community support", "Chat + voice console"],
  },
  {
    title: "Growth",
    price: "$79",
    description: "Production features for scaling teams.",
    bullets: ["Team RBAC", "SLA-backed uptime", "Advanced analytics"],
  },
  {
    title: "Enterprise",
    price: "Custom",
    description: "Security, compliance, and custom SLAs.",
    bullets: ["Dedicated support", "Custom integrations", "Security reviews"],
  },
];

function deriveWsBase(apiBase: string) {
  if (!apiBase) return "";
  if (apiBase.startsWith("https://")) {
    return apiBase.replace("https://", "wss://");
  }
  if (apiBase.startsWith("http://")) {
    return apiBase.replace("http://", "ws://");
  }
  return apiBase;
}

function toBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export default function Home() {
  const [apiBase, setApiBase] = useState(
    process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE
  );
  const [token, setToken] = useState("");
  const [kbUserId, setKbUserId] = useState("");
  const [wsMode, setWsMode] = useState<WsMode>("chat");
  const [chatId, setChatId] = useState<string | null>(null);
  const [status, setStatus] = useState("disconnected");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loginTenant, setLoginTenant] = useState("");
  const [loginUser, setLoginUser] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authNotice, setAuthNotice] = useState<Notice | null>(null);
  const [signupTenant, setSignupTenant] = useState("");
  const [signupUsername, setSignupUsername] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [signupFirstName, setSignupFirstName] = useState("");
  const [signupLastName, setSignupLastName] = useState("");
  const [signupPhone, setSignupPhone] = useState("");
  const [isSigningUp, setIsSigningUp] = useState(false);
  const [otpUsernameOrEmail, setOtpUsernameOrEmail] = useState("");
  const [otpChannel, setOtpChannel] = useState("email");
  const [otpCode, setOtpCode] = useState("");
  const [otpExpiresAt, setOtpExpiresAt] = useState("");
  const [isVerifying, setIsVerifying] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const audioQueueRef = useRef<{ data: string; mime: string }[]>([]);
  const audioPlayingRef = useRef(false);
  const recorderRef = useRef<MediaRecorder | null>(null);

  const wsBase = useMemo(() => {
    return process.env.NEXT_PUBLIC_WS_BASE_URL || deriveWsBase(apiBase);
  }, [apiBase]);

  const statusKey = useMemo(() => {
    return status.toLowerCase().replace(/\s+/g, "-");
  }, [status]);

  const isVoiceActive = wsMode === "voice" && (isRecording || isSpeaking);

  useEffect(() => {
    const savedApi = localStorage.getItem(LOCAL_KEYS.apiBase);
    const savedToken = localStorage.getItem(LOCAL_KEYS.token);
    const savedKb = localStorage.getItem(LOCAL_KEYS.kbUserId);
    const savedMode = localStorage.getItem(LOCAL_KEYS.wsMode) as WsMode | null;

    if (savedApi) setApiBase(savedApi);
    if (savedToken) setToken(savedToken);
    if (savedKb) setKbUserId(savedKb);
    if (savedMode === "chat" || savedMode === "voice") setWsMode(savedMode);
  }, []);

  useEffect(() => {
    localStorage.setItem(LOCAL_KEYS.apiBase, apiBase);
  }, [apiBase]);

  useEffect(() => {
    localStorage.setItem(LOCAL_KEYS.token, token);
  }, [token]);

  useEffect(() => {
    localStorage.setItem(LOCAL_KEYS.kbUserId, kbUserId);
  }, [kbUserId]);

  useEffect(() => {
    localStorage.setItem(LOCAL_KEYS.wsMode, wsMode);
  }, [wsMode]);

  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
      setStatus("disconnected");
    }
  }, [wsMode, apiBase, token]);

  const wsUrl = useMemo(() => {
    if (!wsBase) return "";
    const path = wsMode === "voice" ? "/api/v1/support/voice/ws" : "/api/v1/support/chat/ws";
    const params = new URLSearchParams();
    if (token) params.set("token", token);
    if (chatId) params.set("chat_id", chatId);
    if (kbUserId) params.set("kb_user_id", kbUserId);
    return `${wsBase}${path}?${params.toString()}`;
  }, [wsBase, wsMode, token, chatId, kbUserId]);

  const ensureConnected = async () => {
    if (!token) {
      setStatus("missing token");
      return null;
    }
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return wsRef.current;
    }
    if (!wsUrl) {
      setStatus("missing ws url");
      return null;
    }

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    setStatus("connecting");

    ws.onopen = () => {
      setStatus("connected");
      const payload: Record<string, unknown> = {
        type: "session.start",
      };
      if (chatId) payload.session_id = chatId;
      if (kbUserId) payload.kb_user_id = kbUserId;
      ws.send(JSON.stringify(payload));
    };

    ws.onclose = () => {
      setStatus("disconnected");
    };

    ws.onerror = () => {
      setStatus("error");
    };

    ws.onmessage = (event) => {
      let payload: any = null;
      try {
        payload = JSON.parse(event.data);
      } catch (err) {
        return;
      }

      const type = String(payload.type || "");
      if (type === "session.started") {
        if (payload.chat_id) setChatId(payload.chat_id);
        if (payload.session_id && !payload.chat_id) setChatId(payload.session_id);
        return;
      }

      if (type === "llm.chunk") {
        const chunk = String(payload.content || "");
        if (!chunk) return;
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantIdRef.current
              ? { ...msg, content: msg.content + chunk }
              : msg
          )
        );
        return;
      }

      if (type === "response.done") {
        const text = String(payload.text || "");
        if (payload.chat_id) setChatId(payload.chat_id);
        if (payload.session_id && !payload.chat_id) setChatId(payload.session_id);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantIdRef.current
              ? { ...msg, content: text || msg.content, streaming: false }
              : msg
          )
        );
        assistantIdRef.current = null;
        return;
      }

      if (type === "stt.final") {
        const text = String(payload.text || "");
        if (!text) return;
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "system", content: `STT: ${text}` },
        ]);
        return;
      }

      if (type === "tts.chunk") {
        const audio = String(payload.audio_b64 || "");
        const mime = String(payload.mime_type || "audio/mpeg");
        if (!audio) return;
        enqueueAudio(audio, mime);
        return;
      }

      if (type === "error") {
        const detail = String(payload.detail || "error");
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "system", content: `Error: ${detail}` },
        ]);
      }
    };

    return new Promise<WebSocket | null>((resolve) => {
      const timeout = setTimeout(() => resolve(ws), 5000);
      ws.addEventListener("open", () => {
        clearTimeout(timeout);
        resolve(ws);
      });
    });
  };

  const enqueueAudio = (data: string, mime: string) => {
    audioQueueRef.current.push({ data, mime });
    if (!audioPlayingRef.current) {
      playNextAudio();
    }
  };

  const playNextAudio = () => {
    const next = audioQueueRef.current.shift();
    if (!next) {
      audioPlayingRef.current = false;
      setIsSpeaking(false);
      return;
    }

    audioPlayingRef.current = true;
    setIsSpeaking(true);
    const byteString = atob(next.data);
    const buffer = new Uint8Array(byteString.length);
    for (let i = 0; i < byteString.length; i += 1) {
      buffer[i] = byteString.charCodeAt(i);
    }
    const blob = new Blob([buffer], { type: next.mime });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    const handleFinish = () => {
      URL.revokeObjectURL(url);
      playNextAudio();
    };
    audio.onended = handleFinish;
    audio.onerror = handleFinish;
    audio.play().catch(handleFinish);
  };

  const sendText = async () => {
    const trimmed = input.trim();
    if (!trimmed) return;

    const ws = await ensureConnected();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setStatus("disconnected");
      return;
    }

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmed,
    };
    const assistantMessage: Message = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      streaming: true,
    };
    assistantIdRef.current = assistantMessage.id;

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInput("");

    ws.send(JSON.stringify({ type: "input.text", text: trimmed }));
  };

  const handleLogin = async () => {
    if (!loginTenant || !loginUser || !loginPassword) {
      setAuthNotice({ tone: "error", message: "Enter tenant, username/email, and password." });
      return;
    }
    setIsLoggingIn(true);
    setAuthNotice(null);
    try {
      const response = await fetch(`${apiBase}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: loginTenant,
          username_or_email: loginUser,
          password: loginPassword,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail || `Login failed (${response.status})`);
      }
      setToken(data?.access_token || "");
      setAuthNotice({ tone: "success", message: "Login successful. Token ready." });
    } catch (err) {
      setAuthNotice({
        tone: "error",
        message: err instanceof Error ? err.message : "Login failed.",
      });
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleSignup = async () => {
    if (!signupTenant || !signupUsername || !signupEmail || !signupPassword) {
      setAuthNotice({
        tone: "error",
        message: "Tenant, username, email, and password are required.",
      });
      return;
    }
    setIsSigningUp(true);
    setAuthNotice(null);
    try {
      const response = await fetch(`${apiBase}/api/v1/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: signupTenant,
          username: signupUsername,
          email: signupEmail,
          password: signupPassword,
          phone_e164: signupPhone || null,
          first_name: signupFirstName || null,
          last_name: signupLastName || null,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail || `Signup failed (${response.status})`);
      }
      setOtpChannel(data?.channel || "email");
      setOtpUsernameOrEmail(signupEmail || signupUsername);
      setOtpExpiresAt(
        data?.expires_at ? new Date(data.expires_at).toLocaleString() : ""
      );
      setAuthNotice({
        tone: "info",
        message: `OTP sent to ${data?.destination || "your inbox"}.`,
      });
      setAuthView("verify");
    } catch (err) {
      setAuthNotice({
        tone: "error",
        message: err instanceof Error ? err.message : "Signup failed.",
      });
    } finally {
      setIsSigningUp(false);
    }
  };

  const handleVerifyOtp = async () => {
    if (!signupTenant || !otpUsernameOrEmail || !otpCode) {
      setAuthNotice({
        tone: "error",
        message: "Tenant, username/email, and OTP are required.",
      });
      return;
    }
    setIsVerifying(true);
    setAuthNotice(null);
    try {
      const response = await fetch(`${apiBase}/api/v1/auth/verify-otp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: signupTenant,
          username_or_email: otpUsernameOrEmail,
          channel: otpChannel,
          purpose: "signup",
          otp_code: otpCode,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail || `OTP verification failed (${response.status})`);
      }
      setToken(data?.access_token || "");
      setAuthNotice({ tone: "success", message: "OTP verified. Token issued." });
      setAuthView("login");
      setLoginTenant(signupTenant);
      setLoginUser(otpUsernameOrEmail);
    } catch (err) {
      setAuthNotice({
        tone: "error",
        message: err instanceof Error ? err.message : "OTP verification failed.",
      });
    } finally {
      setIsVerifying(false);
    }
  };

  const clearChat = () => {
    setMessages([]);
    setChatId(null);
    assistantIdRef.current = null;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("disconnected");
  };

  const toggleRecording = async () => {
    if (wsMode !== "voice") {
      return;
    }

    if (isRecording) {
      recorderRef.current?.stop();
      setIsRecording(false);
      return;
    }

    try {
      if (typeof MediaRecorder === "undefined") {
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "system", content: "Recording not supported." },
        ]);
        return;
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferredTypes = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/ogg",
      ];
      const mimeType =
        preferredTypes.find((type) => MediaRecorder.isTypeSupported(type)) || "";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        const buffer = await blob.arrayBuffer();
        const base64 = toBase64(buffer);

        const ws = await ensureConnected();
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          setStatus("disconnected");
          return;
        }

        ws.send(
          JSON.stringify({
            type: "input.audio",
            audio_b64: base64,
            mime_type: blob.type || "audio/webm",
          })
        );

        stream.getTracks().forEach((track) => track.stop());
      };
      recorder.start();
      setIsRecording(true);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "system", content: "Mic access denied." },
      ]);
    }
  };

  const scrollTo = (id: string) => {
    if (typeof document === "undefined") return;
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="landing">
      <div className="announcement">
        <span className="announcement-pill">New</span>
        <span>
          Now shipping voice + streaming support agents. Launch in minutes.
        </span>
          <button className="announcement-link" onClick={() => scrollTo("console")}>
            Open console ->
          </button>
        </div>

      <header className="navbar">
        <div className="brand">
          <span className="brand-mark">K</span>
          <div>
            <div className="brand-name">Knowledge AI Cloud</div>
            <div className="brand-sub">The AI that actually does things</div>
          </div>
        </div>
        <nav className="nav-links">
          <a href="#features">Features</a>
          <a href="#channels">Channels</a>
          <a href="#pricing">Pricing</a>
          <a href="#security">Security</a>
          <a href="#console">Console</a>
        </nav>
        <div className="nav-actions">
          <button className="btn ghost" onClick={() => scrollTo("console")}
          >
            Sign in
          </button>
          <button className="btn primary" onClick={() => scrollTo("console")}
          >
            Get Started
          </button>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <div className="hero-pill">Agent</div>
            <h1>The AI assistant that actually runs your operations.</h1>
            <p>
              Everything your on-prem assistant does - plus secure team access, one-click
              integrations, skill scanning, and unified credits. No servers. No setup.
            </p>
            <div className="hero-actions">
              <button className="btn primary" onClick={() => scrollTo("console")}
              >
                Start free trial
              </button>
              <button className="btn ghost" onClick={() => scrollTo("pricing")}
              >
                View pricing
              </button>
            </div>
            <div className="hero-note">
              7-day free trial. No credit card required. Fully managed.
            </div>
            <div className="channel-row">
              {CHANNELS.map((channel) => (
                <span key={channel} className="chip">
                  {channel}
                </span>
              ))}
            </div>
          </div>

          <div className="hero-card">
            <div className="hero-card-header">
              <div>
                <div className="card-title">Live Agent Pulse</div>
                <div className="card-subtitle">Realtime streaming + voice</div>
              </div>
              <span className={`status-pill status-${statusKey}`}>{status}</span>
            </div>
            <div className={`voice-orb ${isVoiceActive ? "active" : ""}`}>
              <div className="voice-core" />
              <div className="voice-rings">
                <span />
                <span />
                <span />
              </div>
              <div className="voice-eq">
                <span />
                <span />
                <span />
                <span />
                <span />
                <span />
              </div>
            </div>
            <div className="hero-metrics">
              <div>
                <span className="metric-value">{messages.length}</span>
                <span className="metric-label">Events</span>
              </div>
              <div>
                <span className="metric-value">{wsMode.toUpperCase()}</span>
                <span className="metric-label">Mode</span>
              </div>
              <div>
                <span className="metric-value">{chatId ? chatId.slice(0, 6) : "new"}</span>
                <span className="metric-label">Chat</span>
              </div>
            </div>
            <div className="hero-card-actions">
              <button className="btn secondary" onClick={ensureConnected}>
                Start session
              </button>
              <button className="btn ghost" onClick={clearChat}>
                Reset
              </button>
            </div>
          </div>
        </section>

        <section id="channels" className="section">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Channels</div>
              <h2>Works on the platforms your teams already use.</h2>
            </div>
            <p>
              Meet customers in WhatsApp, Telegram, Slack, Discord, and your web chat.
            </p>
          </div>
          <div className="channel-grid">
            {CHANNELS.map((channel) => (
              <div key={channel} className="channel-card">
                <div className="channel-icon">{channel.slice(0, 2).toUpperCase()}</div>
                <div className="channel-name">{channel}</div>
                <div className="channel-desc">Unified inbox, shared memory.</div>
              </div>
            ))}
          </div>
        </section>

        <section id="features" className="section">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Features</div>
              <h2>Everything your self-hosted stack can’t deliver.</h2>
            </div>
            <p>
              Ship production-grade agents with collaboration, security, and reliability
              built in.
            </p>
          </div>
          <div className="feature-grid">
            {FEATURE_CARDS.map((feature) => (
              <div key={feature.title} className="feature-card">
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section comparison">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Cloud vs Self-hosting</div>
              <h2>Cloud removes the heavy lifting.</h2>
            </div>
            <p>Replace operational overhead with secure, fully-managed runs.</p>
          </div>
          <div className="comparison-grid">
            <div className="comparison-card">
              <h3>Self-hosting</h3>
              <ul>
                <li>Manual OAuth and token refresh</li>
                <li>Single-user environments</li>
                <li>Infrastructure maintenance</li>
                <li>Manual security reviews</li>
              </ul>
            </div>
            <div className="comparison-card highlight">
              <h3>Knowledge AI Cloud</h3>
              <ul>
                <li>One-click integrations + auto-refresh</li>
                <li>Team RBAC and audit logs</li>
                <li>Fully managed infra</li>
                <li>AI skill scanning and sandboxed runs</li>
              </ul>
            </div>
          </div>
        </section>

        <section className="section steps">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Live in minutes</div>
              <h2>Launch in three steps.</h2>
            </div>
            <p>Go from signup to production in under an hour.</p>
          </div>
          <div className="step-grid">
            {STEPS.map((step, index) => (
              <div key={step.title} className="step-card">
                <div className="step-number">0{index + 1}</div>
                <h3>{step.title}</h3>
                <p>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="security" className="section security">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Security</div>
              <h2>Security-first. No compromises.</h2>
            </div>
            <p>Enterprise-grade controls for every message and every run.</p>
          </div>
          <div className="security-grid">
            {SECURITY_POINTS.map((point) => (
              <div key={point} className="security-card">
                <div className="security-dot" />
                <p>{point}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="console" className="section console">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Console</div>
              <h2>Production-ready console connected to your backend.</h2>
            </div>
            <p>
              Signup, verify, and stream responses from your Knowledge AI agents in real
              time.
            </p>
          </div>

          <div className="console-grid">
            <div className="panel auth-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Access Portal</div>
                  <div className="panel-subtitle">Signup, login, and OTP verification</div>
                </div>
                <div className="segmented">
                  <button
                    className={authView === "login" ? "active" : ""}
                    onClick={() => setAuthView("login")}
                  >
                    Login
                  </button>
                  <button
                    className={authView === "signup" ? "active" : ""}
                    onClick={() => setAuthView("signup")}
                  >
                    Signup
                  </button>
                  <button
                    className={authView === "verify" ? "active" : ""}
                    onClick={() => setAuthView("verify")}
                  >
                    Verify
                  </button>
                </div>
              </div>

              {authNotice && (
                <div className={`notice ${authNotice.tone}`}>{authNotice.message}</div>
              )}

              {authView === "login" && (
                <div className="form-grid">
                  <div className="field">
                    <label>Tenant ID</label>
                    <input
                      value={loginTenant}
                      onChange={(e) => setLoginTenant(e.target.value)}
                      placeholder="Tenant ID"
                    />
                  </div>
                  <div className="field">
                    <label>Username or Email</label>
                    <input
                      value={loginUser}
                      onChange={(e) => setLoginUser(e.target.value)}
                      placeholder="Username or email"
                    />
                  </div>
                  <div className="field">
                    <label>Password</label>
                    <input
                      type="password"
                      value={loginPassword}
                      onChange={(e) => setLoginPassword(e.target.value)}
                      placeholder="Password"
                    />
                  </div>
                  <div className="form-actions">
                    <button className="btn primary" onClick={handleLogin} disabled={isLoggingIn}>
                      {isLoggingIn ? "Logging in..." : "Login"}
                    </button>
                    <button className="btn ghost" onClick={() => setAuthView("signup")}>
                      Create account
                    </button>
                  </div>
                </div>
              )}

              {authView === "signup" && (
                <div className="form-grid">
                  <div className="field">
                    <label>Tenant ID</label>
                    <input
                      value={signupTenant}
                      onChange={(e) => setSignupTenant(e.target.value)}
                      placeholder="Tenant ID"
                    />
                  </div>
                  <div className="field">
                    <label>Username</label>
                    <input
                      value={signupUsername}
                      onChange={(e) => setSignupUsername(e.target.value)}
                      placeholder="Username"
                    />
                  </div>
                  <div className="field">
                    <label>Email</label>
                    <input
                      value={signupEmail}
                      onChange={(e) => setSignupEmail(e.target.value)}
                      placeholder="Email"
                    />
                  </div>
                  <div className="field">
                    <label>Password</label>
                    <input
                      type="password"
                      value={signupPassword}
                      onChange={(e) => setSignupPassword(e.target.value)}
                      placeholder="Create a password"
                    />
                  </div>
                  <div className="field">
                    <label>First Name</label>
                    <input
                      value={signupFirstName}
                      onChange={(e) => setSignupFirstName(e.target.value)}
                      placeholder="First name"
                    />
                  </div>
                  <div className="field">
                    <label>Last Name</label>
                    <input
                      value={signupLastName}
                      onChange={(e) => setSignupLastName(e.target.value)}
                      placeholder="Last name"
                    />
                  </div>
                  <div className="field">
                    <label>Phone (E.164)</label>
                    <input
                      value={signupPhone}
                      onChange={(e) => setSignupPhone(e.target.value)}
                      placeholder="+1 555 555 5555"
                    />
                  </div>
                  <div className="form-actions">
                    <button
                      className="btn primary"
                      onClick={handleSignup}
                      disabled={isSigningUp}
                    >
                      {isSigningUp ? "Requesting OTP..." : "Create account"}
                    </button>
                    <button className="btn ghost" onClick={() => setAuthView("verify")}>
                      Verify OTP
                    </button>
                  </div>
                </div>
              )}

              {authView === "verify" && (
                <div className="form-grid">
                  <div className="field">
                    <label>Tenant ID</label>
                    <input value={signupTenant} onChange={(e) => setSignupTenant(e.target.value)} />
                  </div>
                  <div className="field">
                    <label>Username or Email</label>
                    <input
                      value={otpUsernameOrEmail}
                      onChange={(e) => setOtpUsernameOrEmail(e.target.value)}
                      placeholder="username or email"
                    />
                  </div>
                  <div className="field">
                    <label>Channel</label>
                    <select value={otpChannel} onChange={(e) => setOtpChannel(e.target.value)}>
                      <option value="email">Email</option>
                      <option value="mobile">Mobile</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>OTP Code</label>
                    <input
                      value={otpCode}
                      onChange={(e) => setOtpCode(e.target.value)}
                      placeholder="Enter code"
                    />
                  </div>
                  {otpExpiresAt && (
                    <div className="helper">OTP expires at {otpExpiresAt}</div>
                  )}
                  <div className="form-actions">
                    <button
                      className="btn primary"
                      onClick={handleVerifyOtp}
                      disabled={isVerifying}
                    >
                      {isVerifying ? "Verifying..." : "Verify OTP"}
                    </button>
                    <button className="btn ghost" onClick={() => setAuthView("login")}>
                      Back to login
                    </button>
                  </div>
                </div>
              )}
            </div>

            <div className="panel settings-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Session Settings</div>
                  <div className="panel-subtitle">Configure endpoints and routing</div>
                </div>
                <span className={`status-pill status-${statusKey}`}>{status}</span>
              </div>

              <div className="field">
                <label>API Base URL</label>
                <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
              </div>

              <div className="field">
                <label>Access Token (JWT)</label>
                <textarea
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Paste access token"
                />
              </div>

              <div className="field">
                <label>KB User ID (optional)</label>
                <input value={kbUserId} onChange={(e) => setKbUserId(e.target.value)} />
              </div>

              <div className="field">
                <label>Mode</label>
                <div className="mode-toggle">
                  <button
                    className={wsMode === "chat" ? "active" : ""}
                    onClick={() => setWsMode("chat")}
                  >
                    Chat
                  </button>
                  <button
                    className={wsMode === "voice" ? "active" : ""}
                    onClick={() => setWsMode("voice")}
                  >
                    Voice
                  </button>
                </div>
              </div>

              <div className="field">
                <label>Current Chat ID</label>
                <input value={chatId || "(new)"} readOnly />
              </div>

              <div className="field">
                <label>WS URL</label>
                <div className="mono-card">{wsUrl || "(not ready)"}</div>
              </div>

              <div className="button-row">
                <button className="btn secondary" onClick={ensureConnected}>
                  Connect
                </button>
                <button className="btn ghost" onClick={clearChat}>
                  New Chat
                </button>
                {wsMode === "voice" && (
                  <button className="btn" onClick={toggleRecording}>
                    {isRecording ? "Stop recording" : "Record"}
                  </button>
                )}
              </div>
            </div>

            <div className="panel chat-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Conversation</div>
                  <div className="panel-subtitle">
                    Streams responses from {wsMode === "voice" ? "voice" : "chat"} agent
                  </div>
                </div>
                <div className="chat-actions">
                  <button className="btn ghost" onClick={clearChat}>
                    New Chat
                  </button>
                  <button className="btn secondary" onClick={ensureConnected}>
                    Reconnect
                  </button>
                </div>
              </div>

              <div className="messages">
                {messages.length === 0 && (
                  <div className="empty-state">
                    Your production feed is empty. Start a session to stream responses.
                  </div>
                )}
                {messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`message ${msg.role} ${msg.streaming ? "streaming" : ""}`}
                  >
                    <div className="message-body">
                      {msg.content || (msg.streaming ? "..." : "")}
                    </div>
                    {msg.streaming && <div className="message-stream">Streaming</div>}
                  </div>
                ))}
              </div>

              <div className="input-area">
                <div className="field">
                  <label>Message</label>
                  <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Ask a support question"
                  />
                </div>
                <div className="input-actions">
                  <button className="btn primary" onClick={sendText}>
                    Send
                  </button>
                  <button className="btn ghost" onClick={() => setInput("")}>
                    Clear
                  </button>
                </div>
              </div>

              <div className="footer-note">
                Tip: voice mode streams TTS audio if your backend has a TTS provider configured.
              </div>
            </div>
          </div>
        </section>

        <section id="pricing" className="section pricing">
          <div className="section-head">
            <div>
              <div className="section-eyebrow">Pricing</div>
              <h2>Choose a plan that scales with your team.</h2>
            </div>
            <p>Upgrade anytime. Enterprise plans include custom SLAs.</p>
          </div>
          <div className="pricing-grid">
            {PRICING.map((plan) => (
              <div key={plan.title} className="pricing-card">
                <div className="pricing-title">{plan.title}</div>
                <div className="pricing-price">{plan.price}</div>
                <p className="pricing-desc">{plan.description}</p>
                <ul>
                  {plan.bullets.map((bullet) => (
                    <li key={bullet}>{bullet}</li>
                  ))}
                </ul>
                <button className="btn secondary" onClick={() => scrollTo("console")}
                >
                  Start now
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="cta">
          <div>
            <h2>Your AI assistant is waiting.</h2>
            <p>
              Built for teams who need real automation — not just chatbots. Launch your
              console today.
            </p>
          </div>
          <button className="btn primary" onClick={() => scrollTo("console")}
          >
            Start your free trial
          </button>
        </section>
      </main>

      <footer className="footer">
        <div className="footer-brand">
          <div>Knowledge AI Cloud</div>
          <span>Deploy assistants across every channel.</span>
        </div>
        <div className="footer-links">
          <button className="ghost-link" onClick={() => scrollTo("features")}
          >
            Features
          </button>
          <button className="ghost-link" onClick={() => scrollTo("pricing")}
          >
            Pricing
          </button>
          <button className="ghost-link" onClick={() => scrollTo("security")}
          >
            Security
          </button>
          <button className="ghost-link" onClick={() => scrollTo("console")}
          >
            Console
          </button>
        </div>
        <div className="footer-meta">(c) 2026 Knowledge AI</div>
      </footer>
    </div>
  );
}
