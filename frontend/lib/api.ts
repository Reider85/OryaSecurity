const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface RequestOptions extends RequestInit {
  token?: string;
}

async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { token, ...fetchOptions } = options;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...fetchOptions,
    headers,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

export interface ScanResult {
  verdict: "allow" | "block";
  reason: string;
  latency_ms: number;
  request_id: string;
  cache_hit: boolean;
  rules_matched: Array<{ rule_id: string; value: string; position: number; severity: string }>;
}

export interface AuditEventResponse {
  id: number;
  ts: string;
  request_id: string;
  tenant_id: string;
  prompt_hash: string;
  prompt_text_redacted: string;
  verdict: string;
  reason: string;
  rules_matched: Array<{ rule_id: string; position: number }>;
  latency_ms: number;
  policy_version: string;
}

export interface MetricsSummaryResponse {
  rps: number;
  block_rate: number;
  avg_latency: number;
  cache_hit_rate: number;
}

export interface Rule {
  id: string;
  name: string;
  type: string;
  pattern: string;
  severity: string;
  action: string;
  version: string;
}

export interface RuleMatch {
  rule_id: string;
  value: string;
  position: number;
  severity: string;
}

export interface CacheEntry {
  prompt_hash: string;
  verdict: string;
  ts: string;
  expires_at: string;
}

export interface CacheStats {
  total_entries: number;
  hit_rate_24h: number;
  memory_usage: number;
  ttl_average: number;
}

export interface Config {
  llm_provider_url: string;
  llm_model: string;
  llm_timeout: number;
  cache_ttl: number;
  cache_max_size: number;
  max_prompt_length: number;
  rate_limit_rps: number;
}

export const api = {
  scan: (prompt: string, token?: string) =>
    request<ScanResult>("/scan", {
      method: "POST",
      body: JSON.stringify({ prompt }),
      token,
    }),

  getAuditEvents: (params?: { 
    limit?: number; 
    page?: number; 
    verdict?: string;
    prompt_hash?: string;
    start_ts?: string;
    end_ts?: string;
  }, token?: string) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.page) searchParams.set("page", String(params.page));
    if (params?.verdict) searchParams.set("verdict", params.verdict);
    if (params?.prompt_hash) searchParams.set("prompt_hash", params.prompt_hash);
    if (params?.start_ts) searchParams.set("start_ts", params.start_ts);
    if (params?.end_ts) searchParams.set("end_ts", params.end_ts);
    const query = searchParams.toString();
    return request<{ items: AuditEventResponse[]; total: number }>(
      `/api/v1/audit${query ? `?${query}` : ""}`,
      { token }
    );
  },

  getMetricsSummary: (token?: string) =>
    request<MetricsSummary>("/api/v1/metrics/summary", { token }),

  getRules: (token?: string) =>
    request<Rule[]>("/api/v1/rules", { token }),

  getRule: (id: string, token?: string) =>
    request<Rule>(`/api/v1/rules/${id}`, { token }),

  saveRule: (id: string, rule: Partial<Rule>, token?: string) =>
    request<Rule>(`/api/v1/rules/${id}`, {
      method: "POST",
      body: JSON.stringify(rule),
      token,
    }),

  testRule: (text: string, token?: string) =>
    request<RuleMatch[]>("/api/v1/rules/test", {
      method: "POST",
      body: JSON.stringify({ text }),
      token,
    }),

  getCacheStats: (token?: string) =>
    request<CacheStats>("/api/v1/cache/stats", { token }),

  getCacheEntries: (limit?: number, token?: string) =>
    request<CacheEntry[]>(`/api/v1/cache?limit=${limit || 50}`, { token }),

  deleteCacheEntry: (hash: string, token?: string) =>
    request<void>(`/api/v1/cache/${hash}`, { method: "DELETE", token }),

  flushCache: (token?: string) =>
    request<void>("/api/v1/cache", { method: "DELETE", token }),

  getConfig: (token?: string) =>
    request<Config>("/api/v1/config", { token }),

  saveConfig: (config: Partial<Config>, token?: string) =>
    request<Config>("/api/v1/config", {
      method: "POST",
      body: JSON.stringify(config),
      token,
    }),

  login: (apiKey: string) =>
    request<{ token: string }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    }),
};
