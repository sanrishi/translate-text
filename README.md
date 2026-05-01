# DeepL Translate Text (`deepl-translate-text`)

Read-only adapter that translates a single UTF-8 text string with the DeepL API.
The source language is auto-detected by DeepL; callers specify `target_lang`.

## What it does

- Calls DeepL `POST /v2/translate` with `text` and `target_lang`
- Returns `translated_text`, `detected_source_lang`, and optional `billed_characters`
- Includes standard provenance fields (`source`, `source_url`, `fetched_at`, `cache_ttl_seconds`)

## Input

```json
{
  "text": "Hello, world!",
  "target_lang": "JA",
  "formality": "default",
  "split_sentences": "1",
  "preserve_formatting": false
}
```

Required:
- `text`: text to translate
- `target_lang`: DeepL language code (e.g. `EN`, `JA`, `DE`, `EN-US`, `PT-BR`)

Optional:
- `formality`: `default` | `more` | `less` | `prefer_more` | `prefer_less`
- `split_sentences`: `0` | `1` | `nonewlines`
- `preserve_formatting`: boolean

## Output (shape)

```json
{
  "summary": "Translated 13 chars to JA via DeepL.",
  "input_text": "Hello, world!",
  "translated_text": "こんにちは、世界！",
  "detected_source_lang": "EN",
  "target_lang": "JA",
  "billed_characters": 13,
  "source": "DeepL API",
  "source_url": "https://developers.deepl.com/api-reference/translate",
  "fetched_at": "2026-05-01T00:00:00Z",
  "cache_ttl_seconds": 0,
  "attribution": "DeepL"
}
```

## Configuration

Set a DeepL API key in the environment:

```powershell
$env:DEEPL_AUTH_KEY = "<your-deepl-auth-key>"
```

Optional:
- `DEEPL_API_BASE_URL`: override base URL (defaults to `https://api-free.deepl.com` for Free keys ending in `:fx`, else `https://api.deepl.com`)
- `DEEPL_TIMEOUT_SECONDS`: request timeout (default `12`)

## Local checks

From this directory:

```powershell
siglume test .
```

`siglume test .` uses `dry_run` and does not call DeepL.

