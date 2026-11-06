# Desktop languages

Choose **ENG / KOR** at the top right of the desktop workspace. The initial default
is Korean for existing users. The non-secret preference `factory.language` (`en`
or `ko`) is stored locally. It takes effect immediately and survives a restart.
If browser storage is unavailable, the selection still works for the session.

Switching captures the current form drafts and renders the same application state.
It does not save connection credentials, invalidate a reviewed plan, submit a job,
transfer data or restart the controller. Project names, imported data, model IDs,
file paths and raw logs retain their original values. Original backend diagnostics
can remain visible alongside translated explanations; OS file dialogs follow the OS.

## Adding messages

`desktop/src/i18n.ts` provides the shared locale and rendering helpers.
`desktop/src/locales/en.json` maps Korean source messages to English; the source
text is also the Korean fallback. No external translation service runs in the app.

- Use `t` (imported as `tr`) for a fixed text label or message.
- Use the `msg` template tag for a sentence with interpolated values. Catalog slots
  are `{0}`, `{1}`, etc.; translations must preserve every slot.
- Use the `html` tag (imported as `ui`) for markup. It translates static text and
  display attributes before inserting dynamic values. Continue escaping dynamic
  values with `escapeHtml` as the existing renderers do; `ui` does not escape them.
- Use `staticHtml` for fixed markup strings.
- Static label tables use `localized(() => (...))` to resolve them in the active
  language on access. Keep user-authored objects out of these tables. Accessor
  properties, such as a project's title, return their dynamic value unchanged.
- Evaluate computed messages when used, rather than caching a translated string
  at module initialization. Keep IDs, enum values, settings keys and API payloads
  independent of display labels.

Unknown diagnostics retain their original text. Add a precise message or existing
issue-code translation rather than replacing a specific failure with generic advice.

## Verification and recordings

`desktop/tests/language.spec.ts` checks immediate switching, persistence, original
user text, credential drafts, unchanged plans, translated navigation/tutorials and
catalog placeholders. The existing Korean UI suite remains enabled.

Run `npm run build` and `npm test` from `desktop/`, plus the repository's required
Python checks. [GIF reproduction](images/README.md) records the same four frontend
scenarios in each language using local mocks; it does not start a GPU run.
