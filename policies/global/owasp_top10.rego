package scanner.global

# OWASP LLM Top 10 (2025) mapping
owasp_top10 := {
    "LLM01": "Prompt Injection",
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Supply Chain",
    "LLM04": "Data and Model Poisoning",
    "LLM05": "Improper Output Handling",
    "LLM06": "Excessive Agency",
    "LLM07": "System Prompt Leakage",
    "LLM08": "Vector and Embedding Weaknesses",
    "LLM09": "Misinformation",
    "LLM10": "Unbounded Consumption"
}

# Collect OWASP tags present in input
owasp_tags["LLM01"] {
    c := input.detectors.ml_classifier
    c.top_class == "prompt_injection"
}
owasp_tags["LLM01"] {
    c := input.detectors.ml_classifier
    c.top_class == "jailbreak"
}
owasp_tags["LLM02"] {
    c := input.detectors.ml_classifier
    c.top_class == "pii"
}
owasp_tags["LLM02"] {
    c := input.detectors.ml_classifier
    c.top_class == "secrets"
}
owasp_tags["LLM08"] {
    c := input.detectors.ml_classifier
    c.top_class == "vector_attack"
}
owasp_tags["LLM02"] {
    m := input.detectors.rules.matches[_]
    startswith(m.rule_id, "pii_")
}
owasp_tags["LLM02"] {
    m := input.detectors.rules.matches[_]
    startswith(m.rule_id, "secret_")
}