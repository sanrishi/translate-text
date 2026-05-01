# MyMemory Translate Text (`mymemory-translate-text`)

Read-only adapter that translates a single UTF-8 text string with the MyMemory Translation API.
`source_lang` defaults to `en` when omitted; callers specify `target_lang`.

## What it does

- Calls MyMemory `GET /get` with `q` and `langpair=<source>|<target>`
- Returns `translated_text`, `source_lang`, and `target_lang`
- Includes standard provenance fields (`source`, `source_url`, `fetched_at`, `cache_ttl_seconds`)

## Input

```json
{
  "text": "Hello, world!",
  "source_lang": "en",
  "target_lang": "ja"
}
```

Required:
- `text`: text to translate
- `source_lang`: (optional) language code, defaults to `en`
- `target_lang`: language code (e.g. `ja`, `fr`, `de`)

## Output (shape)

```json
{
  "summary": "Translated 13 chars en->ja via MyMemory.",
  "input_text": "Hello, world!",
  "translated_text": "こんにちは、世界！",
  "source_lang": "en",
  "target_lang": "ja",
  "source": "MyMemory Translation API",
  "source_url": "https://api.mymemory.translated.net/get",
  "fetched_at": "2026-05-01T00:00:00Z",
  "cache_ttl_seconds": 0,
  "attribution": "MyMemory"
}
```

## Configuration

No API key required.

Optional:
- `MYMEMORY_TIMEOUT_SECONDS`: request timeout (default `12`)

## Local checks

From this directory:

```powershell
siglume test .
```

`siglume test .` uses `dry_run` and does not call DeepL.
