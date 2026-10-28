package tenant_default

import future.keywords.in

# Unit tests for tenants/default.rego (EP-12 Prompt 30).
# Run: opa test policies/ -v

# --- ML classifier: prompt injection -------------------------------------------------

test_allow_clean_prompt {
	allow with input as {"detectors": {"ml_classifier": {"top_class": "benign", "confidence": 0.95}}}
	count(deny) == 0 with input as {"detectors": {"ml_classifier": {"top_class": "benign", "confidence": 0.95}}}
}

test_block_prompt_injection {
	d := deny with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.9}}}
	count(d) == 1
	some msg in d
	startswith(msg, "Blocked: prompt injection")
	contains(msg, "confidence 0.90")
	not allow with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.9}}}
}

test_allow_injection_at_exact_threshold {
	# threshold is strict (>): confidence == 0.85 must NOT block
	allow with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.85}}}
	count(deny) == 0 with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.85}}}
}

test_allow_low_confidence_injection {
	allow with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.5}}}
	count(deny) == 0 with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.5}}}
}

# --- ML classifier: jailbreak --------------------------------------------------------

test_block_jailbreak {
	d := deny with input as {"detectors": {"ml_classifier": {"top_class": "jailbreak", "confidence": 0.9}}}
	count(d) == 1
	some msg in d
	startswith(msg, "Blocked: jailbreak")
	not allow with input as {"detectors": {"ml_classifier": {"top_class": "jailbreak", "confidence": 0.9}}}
}

test_allow_low_confidence_jailbreak {
	allow with input as {"detectors": {"ml_classifier": {"top_class": "jailbreak", "confidence": 0.4}}}
}

# --- Rule engine severity gate ------------------------------------------------------

test_block_critical_rule {
	d := deny with input as {"detectors": {"rules": {"matches": [{
		"rule_id": "secret_aws_key",
		"severity": "critical",
	}]}}}
	count(d) == 1
	some msg in d
	contains(msg, "secret_aws_key")
	contains(msg, "critical")
}

test_block_high_severity_rule {
	count(deny) == 1 with input as {"detectors": {"rules": {"matches": [{
		"rule_id": "pii_ssn_us",
		"severity": "high",
	}]}}}
}

test_allow_medium_severity_rule {
	allow with input as {"detectors": {"rules": {"matches": [{
		"rule_id": "pii_email",
		"severity": "medium",
	}]}}}
	count(deny) == 0 with input as {"detectors": {"rules": {"matches": [{
		"rule_id": "pii_email",
		"severity": "medium",
	}]}}}
}

test_allow_when_no_matches {
	allow with input as {"detectors": {"rules": {"matches": []}, "pii": {"found": []}}}
}

# --- Aggregation --------------------------------------------------------------------

test_multiple_deny_reasons {
	d := deny with input as {"detectors": {
		"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.9},
		"rules": {"matches": [{"rule_id": "secret_aws_key", "severity": "critical"}]},
	}}
	count(d) == 2
	some injection_msg in d
	startswith(injection_msg, "Blocked: prompt injection")
	some rule_msg in d
	contains(rule_msg, "secret_aws_key")
	not allow with input as {"detectors": {
		"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.9},
		"rules": {"matches": [{"rule_id": "secret_aws_key", "severity": "critical"}]},
	}}
}

# --- redact -------------------------------------------------------------------------

test_redact_from_pii_detector {
	r := redact with input as {"detectors": {"pii": {"found": [{"type": "ssn"}, {"type": "email"}]}}}
	count(r) == 2
	r.ssn
	r.email
}

test_redact_from_pii_rules_only {
	r := redact with input as {"detectors": {"rules": {"matches": [
		{"rule_id": "pii_ssn_us", "severity": "low"},
		{"rule_id": "pii_email", "severity": "low"},
		{"rule_id": "secret_aws_key", "severity": "critical"},
	]}}}
	count(r) == 2
	r.pii_ssn_us
	r.pii_email
	not r.secret_aws_key
}

test_no_redaction_for_clean_input {
	count(redact) == 0 with input as {"detectors": {"rules": {"matches": []}, "pii": {"found": []}}}
}
