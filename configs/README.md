# Model Configs

Model configurations are now defined **inline** in `config.json` under the `models` array. The `configs/` directory is kept for backward compatibility only.

## Current approach (inline in config.json)

```json
{
    "models": [
        {
            "name": "my-model",
            "env": {
                "ANTHROPIC_API_KEY": "sk-xxx",
                "ANTHROPIC_BASE_URL": "https://api.provider.com"
            },
            "model": "model-id",
            "thinking": { "type": "enabled", "budget_tokens": 16000 }
        }
    ]
}
```

- `name`: display name used in reports (no spaces)
- `env`: environment variables passed to Claude Code
- `model`: model identifier for the API
- `thinking`: thinking/budget config

See [config.example.json](../config.example.json) for a complete example.

## Legacy (configs/*.json files)

Flat JSON files in this directory are still supported for backward compatibility, but inline configs are preferred.
