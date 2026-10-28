package scanner.global

# Unit tests for global/default_actions.rego (EP-12 Prompt 30).
# Run: opa test policies/ -v

test_default_allow_is_true {
	allow with input as {}
}

test_default_allow_with_detectors {
	allow with input as {"detectors": {"rules": {"matches": []}}}
}

test_block_severities_exact_set {
	block_severities == {"high", "critical"}
}

test_block_severities_membership {
	block_severities.critical
	block_severities.high
	not block_severities.medium
	not block_severities.low
}

test_min_injection_confidence {
	min_injection_confidence == 0.85
}
