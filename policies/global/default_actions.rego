package scanner.global

# Default actions when no specific rule matches (ADR-0004)
default allow := true

# Severity levels requiring block by default (matches SCANNER_PDP_BLOCK_ON_SEVERITY)
block_severities := {"high", "critical"}

# Minimum ML confidence to block prompt injection
min_injection_confidence := 0.85