# Rules Documentation

This directory contains YAML rule files for the LLM Security Scanner. Rules define what patterns should be detected and how to handle them.

## Rule File Format

Each YAML file contains a `rules` array with rule objects. The schema is defined in `../app/core/rules/schema.json`.

### Basic Rule Structure

```yaml
rules:
  - id: rule_identifier
    name: "Human-readable Rule Name"
    type: regex
    pattern: 'regular_expression_pattern'
    severity: low|medium|high|critical
    action: allow|block|log_only
    version: "1.0.0"
    description: "Optional description of what this rule detects"
    enabled: true
```

### Required Fields

- `id`: Unique identifier (alphanumeric with underscores, must start with letter)
- `name`: Human-readable name
- `type`: Currently only "regex" is supported
- `pattern`: Regular expression pattern to match
- `severity`: One of "low", "medium", "high", "critical"
- `action`: One of "allow", "block", "log_only"
- `version: Semantic version (e.g., "1.0.0")

### Optional Fields

- `description`: Description of what the rule detects
- `enabled`: Boolean to enable/disable rule (default: true)

### Action Types

- `allow`: Match but don't block (use with log_only or severity-based blocking)
- `block`: Block the request when matched
- `log_only`: Log the match but don't block

### Severity Levels

- `low`: Low priority, informational logging
- `medium`: Medium priority, moderate blocking consideration
- `high`: High priority, block unless overridden
- `critical`: Critical priority, always block

## Example Rules

### PII Detection

```yaml
rules:
  - id: pii_ssn_us
    name: "US Social Security Number"
    type: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    severity: high
    action: block
    version: "1.0.0"
    description: "Detects US Social Security Numbers in format XXX-XX-XXXX"
```

### Secrets Detection

```yaml
rules:
  - id: secret_aws_key
    name: "AWS Access Key"
    type: regex
    pattern: '\bAKIA[0-9A-Z]{16}\b'
    severity: critical
    action: block
    version: "1.0.0"
    description: "Detects AWS access keys starting with AKIA"
```

### Email Detection (with log_only action)

```yaml
rules:
  - id: pii_email
    name: "Email Address"
    type: regex
    pattern: '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
    severity: medium
    action: log_only
    version: "1.0.0"
    description: "Detects email addresses"
```

## Rule Management

### Adding New Rules

1. Create a new YAML file in this directory (e.g., `custom_rules.yaml`)
2. Add rule definitions following the schema
3. The scanner will automatically load all YAML files on startup

### Modifying Existing Rules

1. Edit the YAML file directly
2. Changes are automatically reloaded (hot-reload enabled)
3. Rules are validated against the schema before loading

### Disabling Rules

Set `enabled: false` to temporarily disable a rule:

```yaml
rules:
  - id: pii_ssn_us
    name: "US Social Security Number"
    type: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    severity: high
    action: block
    version: "1.0.0"
    enabled: false  # Rule disabled
```

## Regular Expression Guidelines

### Pattern Best Practices

1. **Use word boundaries** `\b` for whole-word matching
2. **Anchor patterns** when possible to avoid partial matches
3. **Test patterns** thoroughly with various inputs
4. **Avoid greedy quantifiers** `.*` when possible
5. **Use non-capturing groups** `(?:...)` for performance

### Common Patterns

- **SSN**: `\b\d{3}-\d{2}-\d{4}\b`
- **Email**: `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`
- **AWS Key**: `\bAKIA[0-9A-Z]{16}\b`
- **JWT**: `\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b`

## Testing Rules

### Manual Testing

1. Use the `/test` endpoint in the web UI to test rules
2. Enter text and see which rules match
3. Check the matched positions and severity levels

### Automated Testing

Rules are automatically tested in the test suite. Test cases are stored in `../tests/fixtures/`.

## Performance Considerations

1. **Compiled Patterns**: Rules are compiled to regex patterns on load
2. **Hot Reload**: Changes are automatically detected and reloaded
3. **Caching**: Rule matches are cached for performance
4. **Validation**: Rules are validated against schema before loading

## Troubleshooting

### Common Issues

1. **Invalid YAML**: Check syntax with YAML validators
2. **Invalid Regex**: Test patterns with regex testers
3. **Schema Errors**: Validate against `schema.json`
4. **Loading Errors**: Check logs for specific error messages

### Debug Mode

Enable debug logging to see detailed rule loading information:

```bash
export SCANNER_LOG_LEVEL=DEBUG
```

## Security Notes

1. **PII Redaction**: Matched values are hashed in logs for privacy
2. **Position Tracking**: Exact positions are recorded for auditing
3. **Rule Validation**: All rules are validated before loading
4. **Safe Defaults**: Invalid rules are skipped with warnings

## API Integration

Rules are accessible through the API:

- `GET /api/v1/rules` - List all rules
- `GET /api/v1/rules/{id}` - Get specific rule
- `POST /api/v1/rules/test` - Test text against rules

## Version History

- **1.0.0**: Initial rule format with schema validation and hot-reload support