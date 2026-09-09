# Repository working rules

- 日本語で簡潔かつ丁寧に回答する。
- Always apply the `ponytail:ponytail` skill in this repository, at `full`
  intensity unless the user changes it. Read the installed skill at the start of
  work; prefer existing code, the standard library, and native platform features.
  Preserve correctness, data safety, accessibility, and explicit requirements.
- Prioritize simple UI and a clear path to biodiversity reports linking search,
  detection, and validation-based reliability assessment. Keep only features
  needed for that outcome. Do not add rarely used options "just in case" or
  retain unnecessary options behind advanced settings. Prefer sensible defaults
  when users do not need to make a meaningful choice. Preserve evidence,
  uncertainty, and reproducibility while simplifying the interface.
- Code and code comments are English. UI text must use locale-keyed message
  resources from the first implementation. Ship Japanese and English UI in the
  first preview for the domestic event; Japanese environments default to Japanese.
  Keep the locale selectable and use English as the fallback language. Verify
  Japanese coverage of all preview user flows, including errors and progress text.
  Requirements discussions and handoff documents may be Japanese.
- Recording times use the installation site's IANA timezone, independently of
  the app locale and the PC timezone. Preserve the original timestamp and the
  timezone used at import; changing locale must never change recording instants.
- Product requirements are being reviewed in `docs/requirements.md`.
  Keep inherited decisions, proposals, verified evidence, and open questions
  distinct. Do not mark a proposal accepted without the user's agreement.
