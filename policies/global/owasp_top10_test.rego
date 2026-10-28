package scanner.global

# Unit tests for global/owasp_top10.rego (EP-12 Prompt 30).
# Run: opa test policies/ -v

test_owasp_top10_has_all_categories {
	count(owasp_top10) == 10
	owasp_top10.LLM01 == "Prompt Injection"
	owasp_top10.LLM02 == "Sensitive Information Disclosure"
	owasp_top10.LLM08 == "Vector and Embedding Weaknesses"
	owasp_top10.LLM10 == "Unbounded Consumption"
}

test_prompt_injection_maps_to_llm01 {
	t := owasp_tags with input as {"detectors": {"ml_classifier": {"top_class": "prompt_injection", "confidence": 0.9}}}
	t.LLM01
}

test_jailbreak_maps_to_llm01 {
	t := owasp_tags with input as {"detectors": {"ml_classifier": {"top_class": "jailbreak", "confidence": 0.9}}}
	t.LLM01
}

test_pii_top_class_maps_to_llm02 {
	t := owasp_tags with input as {"detectors": {"ml_classifier": {"top_class": "pii", "confidence": 0.9}}}
	t.LLM02
}

test_secrets_top_class_maps_to_llm02 {
	t := owasp_tags with input as {"detectors": {"ml_classifier": {"top_class": "secrets", "confidence": 0.9}}}
	t.LLM02
}

test_vector_attack_maps_to_llm08 {
	t := owasp_tags with input as {"detectors": {"ml_classifier": {"top_class": "vector_attack", "confidence": 0.9}}}
	t.LLM08
}

test_pii_rule_match_maps_to_llm02 {
	t := owasp_tags with input as {"detectors": {"rules": {"matches": [{"rule_id": "pii_ssn_us", "severity": "high"}]}}}
	t.LLM02
}

test_secret_rule_match_maps_to_llm02 {
	t := owasp_tags with input as {"detectors": {"rules": {"matches": [{"rule_id": "secret_aws_key", "severity": "critical"}]}}}
	t.LLM02
}

test_no_tags_for_clean_input {
	count(owasp_tags) == 0 with input as {"detectors": {"ml_classifier": {"top_class": "benign", "confidence": 0.95}}}
}
