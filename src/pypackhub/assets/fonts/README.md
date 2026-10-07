# Bundled fonts

PyPackHub ships its UI fonts so that the interface looks identical on every
platform. All three families are licensed under the SIL Open Font License 1.1
(OFL), which permits redistribution inside this application as long as the
license text travels with the fonts — the `OFL.txt` file next to each family is
that license.

| Family | Weights bundled | Upstream | Version pulled |
| --- | --- | --- | --- |
| Inter | Regular, Medium, SemiBold, Bold | <https://github.com/rsms/inter> | 4.1 (`extras/ttf/` static instances) |
| JetBrains Mono | Regular, Medium, SemiBold, Bold | <https://github.com/JetBrains/JetBrainsMono> | `master` (`fonts/ttf/`) |
| Vazirmatn | Regular, Medium, SemiBold, Bold | <https://github.com/rastikerdar/vazirmatn> | `master` (`fonts/ttf/`) |

Roles in the UI:

- **Inter** — the general UI font (13px body, 12px tables, 20px titles).
- **JetBrains Mono** — logs, version numbers, commands and any other text that
  must line up in columns.
- **Vazirmatn** — the same UI roles as Inter, but for Persian (RTL) mode.

Static instances are used instead of variable fonts on purpose: Qt's handling
of variable-font axes is inconsistent across platforms, while static faces load
predictably everywhere.

## Updating a font

1. Download the new static TTFs from the upstream release.
2. Replace the files in the matching directory, keeping the upstream names.
3. Replace `OFL.txt` if the upstream license text changed.
4. Update the version column above.
5. Re-launch the app and check the log panel and the Persian UI, since glyph
   metrics change between releases.
