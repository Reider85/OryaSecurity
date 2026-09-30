import { parse, stringify } from "yaml";

export interface RuleDraft {
  id: string;
  name: string;
  type: string;
  pattern: string;
  severity: string;
  action: string;
  version: string;
  description?: string | null;
  enabled?: boolean;
}

export const RULE_SEVERITIES = ["low", "medium", "high", "critical"] as const;
export const RULE_ACTIONS = ["allow", "block", "log_only"] as const;
const ID_PATTERN = /^[a-z][a-z0-9_]*$/;
const SEMVER_PATTERN = /^\d+\.\d+\.\d+$/;

export const NEW_RULE_TEMPLATE: RuleDraft = {
  id: "new_rule_id",
  name: "New Rule",
  type: "regex",
  pattern: "",
  severity: "medium",
  action: "block",
  version: "1.0.0",
  description: "",
  enabled: true,
};

export function ruleToYaml(rule: Partial<RuleDraft>): string {
  const data: Record<string, unknown> = {
    id: rule.id ?? "",
    name: rule.name ?? "",
    type: rule.type ?? "regex",
    pattern: rule.pattern ?? "",
    severity: rule.severity ?? "medium",
    action: rule.action ?? "block",
    version: rule.version ?? "1.0.0",
  };
  if (rule.description != null) data.description = rule.description;
  if (rule.enabled != null) data.enabled = rule.enabled;
  return stringify(data, { lineWidth: 0 });
}

export function validateRuleDraft(rule: RuleDraft): string[] {
  const errors: string[] = [];

  if (!rule.id) {
    errors.push("id is required");
  } else if (!ID_PATTERN.test(rule.id)) {
    errors.push('id must match ^[a-z][a-z0-9_]*$ (e.g. pii_ssn_us)');
  }

  if (!rule.name) errors.push("name is required");
  if (!rule.type) {
    errors.push("type is required");
  } else if (rule.type !== "regex") {
    errors.push('type must be "regex"');
  }

  if (rule.pattern == null || rule.pattern === "") {
    errors.push("pattern is required");
  } else if (rule.type === "regex") {
    try {
      new RegExp(rule.pattern);
    } catch {
      errors.push("pattern is not a valid regular expression");
    }
  }

  if (!rule.severity) {
    errors.push("severity is required");
  } else if (!(RULE_SEVERITIES as readonly string[]).includes(rule.severity)) {
    errors.push(`severity must be one of: ${RULE_SEVERITIES.join(", ")}`);
  }

  if (!rule.action) {
    errors.push("action is required");
  } else if (!(RULE_ACTIONS as readonly string[]).includes(rule.action)) {
    errors.push(`action must be one of: ${RULE_ACTIONS.join(", ")}`);
  }

  if (!rule.version) {
    errors.push("version is required");
  } else if (!SEMVER_PATTERN.test(rule.version)) {
    errors.push('version must be semver, e.g. "1.0.0"');
  }

  return errors;
}

export function yamlToRule(text: string): { rule?: RuleDraft; errors: string[] } {
  let parsed: unknown;
  try {
    parsed = parse(text);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return { errors: [`YAML parse error: ${message}`] };
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { errors: ["Rule must be a YAML mapping (key: value pairs)"] };
  }

  const rule = parsed as RuleDraft;
  return { rule, errors: validateRuleDraft(rule) };
}
