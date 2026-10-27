package tenant_default

import data.scanner.global.block_severities
import data.scanner.global.min_injection_confidence

allow {
	count(deny) == 0
}

allow := false {
	count(deny) > 0
}

deny[msg] {
	c := input.detectors.ml_classifier
	c.top_class == "prompt_injection"
	c.confidence > min_injection_confidence
	msg := sprintf("Blocked: prompt injection (confidence %.2f)", [c.confidence])
}

deny[msg] {
	c := input.detectors.ml_classifier
	c.top_class == "jailbreak"
	c.confidence > min_injection_confidence
	msg := sprintf("Blocked: jailbreak (confidence %.2f)", [c.confidence])
}

deny[msg] {
	m := input.detectors.rules.matches[_]
	block_severities[m.severity]
	msg := sprintf("Blocked: rule %s matched (severity %s)", [m.rule_id, m.severity])
}

redact[pii_type] {
	pii_type := input.detectors.pii.found[_].type
}

redact[label] {
	m := input.detectors.rules.matches[_]
	startswith(m.rule_id, "pii_")
	label := m.rule_id
}
