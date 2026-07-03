# Model Configs

Place model configuration JSON files here. Each file defines one model endpoint.

## Format

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-xxx",
    "ANTHROPIC_BASE_URL": "https://api.provider.com"
  },
  "model": "model-id",
  "thinking": { "type": "enabled", "budget_tokens": 16000 }
}
```

## Example

`opus-direct.json` → Anthropic official Opus
`sonnet-openrouter.json` → Sonnet via OpenRouter

**Note:** All `*.json` files in this directory are gitignored. See `configs/README.md.example` for a documented template.
