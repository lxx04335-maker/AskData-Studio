import type { AuthUser, ConversationDetail, ConversationSummary, LoginResponse, QueryResult, SavedMemory, SchemaField, SchemaTable, WorkspaceConfig } from "./types"

const API_BASE = import.meta.env.VITE_API_BASE ?? ""
const TOKEN_KEY = "askdata_access_token"

function token() {
  return sessionStorage.getItem(TOKEN_KEY)
}

export interface StreamEvent {
  event: "progress" | "text" | "table" | "result" | "error"
  stage?: string
  message?: string
  field?: string
  text?: string
  columns?: string[]
  rows?: QueryResult["rows"]
  sql?: string
  result?: QueryResult
}

async function stream(path: string, body: unknown, onEvent: (event: StreamEvent) => void): Promise<QueryResult> {
  const controller = new AbortController()
  let timer = window.setTimeout(() => controller.abort(), 90_000)
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST", signal: controller.signal,
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", Authorization: `Bearer ${token() ?? ""}` },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const error = await response.json().catch(() => ({}))
      throw new Error(error.detail ?? "请求失败，请重试")
    }
    if (!response.body) throw new Error("浏览器无法读取流式响应")
    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let result: QueryResult | undefined
    while (true) {
      const chunk = await reader.read()
      window.clearTimeout(timer)
      timer = window.setTimeout(() => controller.abort(), 90_000)
      buffer += decoder.decode(chunk.value, { stream: !chunk.done }).replace(/\r\n/g, "\n")
      let boundary: number
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n")
        if (!data) continue
        const event = JSON.parse(data) as StreamEvent
        if (event.event === "error") throw new Error(event.message || "生成中断，请重试")
        onEvent(event)
        if (event.event === "result") result = event.result
      }
      if (chunk.done) break
    }
    if (!result) throw new Error("连接已断开，回复未完成，请重试")
    return result
  } catch (error) {
    if (controller.signal.aborted) throw new Error("连接超时，请重试")
    throw error
  } finally {
    window.clearTimeout(timer)
    await reader?.cancel().catch(() => {})
    reader?.releaseLock()
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 90_000)
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        ...options?.headers,
      },
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("请求超时，请检查模型接口配置后重试")
    }
    throw new Error("无法连接后端服务，请先启动 FastAPI（127.0.0.1:8000）")
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? "服务暂时不可用")
  }
  return response.json() as Promise<T>
}

export const api = {
  hasSession: () => Boolean(token()),
  login: async (username: string, password: string) => {
    const result = await request<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    })
    sessionStorage.setItem(TOKEN_KEY, result.access_token)
    return result.user
  },
  me: () => request<AuthUser>("/api/auth/me"),
  logout: async () => {
    try {
      if (token()) await request<{ logged_out: boolean }>("/api/auth/logout", { method: "POST" })
    } finally {
      sessionStorage.removeItem(TOKEN_KEY)
    }
  },
  clearSession: () => sessionStorage.removeItem(TOKEN_KEY),
  schema: () => request<SchemaTable[]>("/api/schema"),
  conversations: () => request<ConversationSummary[]>("/api/conversations"),
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`),
  createConversation: () => request<ConversationSummary>("/api/conversations", { method: "POST" }),
  updateConversation: (id: string, workspace: WorkspaceConfig) => request<ConversationSummary>(
    `/api/conversations/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ workspace }) },
  ),
  deleteConversation: (id: string) => request<{ deleted: boolean }>(
    `/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" },
  ),
  queryStream: (query: string, workspace: WorkspaceConfig, sessionId: string, onEvent: (event: StreamEvent) => void) =>
    stream("/api/query/stream", { query, session_id: sessionId, workspace }, onEvent),
  clarifyStream: (taskId: string, optionId: string, onEvent: (event: StreamEvent) => void) =>
    stream(`/api/tasks/${taskId}/clarify/stream`, { option_id: optionId }, onEvent),
  query: (query: string, workspace: WorkspaceConfig, sessionId: string) =>
    request<QueryResult>("/api/query", {
      method: "POST",
      body: JSON.stringify({ query, session_id: sessionId, workspace }),
    }),
  clarify: (taskId: string, optionId: string) =>
    request<QueryResult>(`/api/tasks/${taskId}/clarify`, {
      method: "POST",
      body: JSON.stringify({ option_id: optionId }),
    }),
  save: (taskId: string) =>
    request<{ saved: boolean }>("/api/memories", {
      method: "POST",
      body: JSON.stringify({ task_id: taskId }),
    }),
  memories: () => request<SavedMemory[]>("/api/memories"),
  saveField: (tableId: string, field: SchemaField) =>
    request<{ saved: boolean }>("/api/memories/fields", {
      method: "POST",
      body: JSON.stringify({
        table_id: tableId,
        name: field.name,
        label: field.label,
        field_type: field.type,
      }),
    }),
  deleteMemory: (memoryId: string) =>
    request<{ deleted: boolean }>(`/api/memories/${encodeURIComponent(memoryId)}`, {
      method: "DELETE",
    }),
}
