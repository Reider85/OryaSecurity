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

  // /api/auth/* are Next.js Route Handlers on this origin (cookie set/read
  // for UI auth). All other endpoints go to the scanner backend.
  const url = endpoint.startsWith("/api/auth/")
    ? endpoint
    : `${API_BASE}${endpoint}`;

  const res = await fetch(url, {
    ...fetchOptions,
    headers,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  if (!text) {
    return undefined as T;
  }

  return JSON.parse(text) as T;
}

export interface RuleMatch {
  rule_id: string;
  rule_name: string;
  value_hash: string;
  position: [number, number];
  severity: string;
  action: string;
}

export interface ScanResult {
  verdict: "allow" | "block";
  reason: string;
  latency_ms: number;
  request_id: string;
  cache_hit: boolean;
  rules_matched: RuleMatch[];
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

export interface RuleMatchDetail {
  rule_id: string;
  rule_name: string;
  severity: string;
  action: string;
  position?: [number, number];
  matched_value?: string;
}

export interface LatencyBreakdown {
  fast_path_ms: number;
  slow_path_ms: number;
  pdp_ms: number;
  audit_ms: number;
  total_ms: number;
}

export interface DecisionDetail {
  request_id: string;
  tenant_id?: string;
  prompt_hash: string;
  prompt_text_redacted: string;
  verdict: string;
  reason: string;
  rules_matched: RuleMatchDetail[];
  policy_version: string;
  cache_status: "HIT" | "MISS";
  latency_breakdown: LatencyBreakdown;
  created_at: string;
  updated_at: string;
}

export interface DecisionListResponse {
  items: DecisionDetail[];
  total: number;
  page: number;
  per_page: number;
  has_next: boolean;
  has_prev: boolean;
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
  description?: string | null;
  enabled: boolean;
  source_file: string;
}

export interface ReloadRulesResponse {
  status: string;
  total_rules: number;
  enabled_rules: number;
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
  scanPrompt: (prompt: string, token?: string) =>
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
    return request<{ items: AuditEventResponse[]; total: number; page: number; per_page: number }>(
      `/api/v1/audit${query ? `?${query}` : ""}`,
      { token }
    );
  },

  getDecisions: (params?: { 
    limit?: number; 
    page?: number; 
    verdict?: string;
    tenant_id?: string;
    rule_id?: string;
    start_date?: string;
    end_date?: string;
  }, token?: string) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.page) searchParams.set("page", String(params.page));
    if (params?.verdict) searchParams.set("verdict", params.verdict);
    if (params?.tenant_id) searchParams.set("tenant_id", params.tenant_id);
    if (params?.rule_id) searchParams.set("rule_id", params.rule_id);
    if (params?.start_date) searchParams.set("start_date", params.start_date);
    if (params?.end_date) searchParams.set("end_date", params.end_date);
    const query = searchParams.toString();
    return request<DecisionListResponse>(
      `/api/v1/decisions${query ? `?${query}` : ""}`,
      { token }
    );
  },

  getDecisionDetail: (requestId: string, token?: string) =>
    request<DecisionDetail>(`/api/v1/decisions/${requestId}`, { token }),

  getMetricsSummary: (token?: string) =>
    request<MetricsSummaryResponse>("/api/v1/metrics/summary", { token }),

  getRequestChartData: (params?: { hours?: number }, token?: string) =>
    request<any[]>("/api/v1/charts/requests", {
      params: { hours: params?.hours || 24 },
      token
    }),

  getRulesChartData: (params?: { days?: number }, token?: string) =>
    request<any[]>("/api/v1/charts/rules", {
      params: { days: params?.days || 7 },
      token
    }),

  getRealtimeChartData: (params?: { minutes?: number }, token?: string) =>
    request<any[]>("/api/v1/charts/realtime", {
      params: { minutes: params?.minutes || 60 },
      token
    }),

  getRules: (token?: string) =>
    request<{ items: Rule[]; total: number; last_loaded_time?: number }>(
      "/api/v1/rules",
      { token }
    ),

  getRule: (id: string, token?: string) =>
    request<Rule>(`/api/v1/rules/${id}`, { token }),

  saveRule: (id: string, rule: Partial<Rule>, token?: string) =>
    request<Rule>(`/api/v1/rules/${id}`, {
      method: "POST",
      body: JSON.stringify(rule),
      token,
    }),

  createRule: (rule: Partial<Rule>, sourceFile: string, token?: string) =>
    request<Rule>("/api/v1/rules", {
      method: "POST",
      body: JSON.stringify({ source_file: sourceFile, rule }),
      token,
    }),

  reloadRules: (token?: string) =>
    request<ReloadRulesResponse>("/api/v1/rules/reload", {
      method: "POST",
      token,
    }),

  testRule: (text: string, ruleId?: string, token?: string) =>
    request<RuleMatch[]>("/api/v1/rules/test", {
      method: "POST",
      body: JSON.stringify(ruleId ? { text, rule_id: ruleId } : { text }),
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
    request<{ token: string; token_type?: string; expires_in?: number; tenant_id?: string }>(
      "/api/auth/login",
      {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey }),
      }
    ),

  logout: () =>
    request<{ ok: boolean }>("/api/auth/logout", {
      method: "POST",
    }),
};
