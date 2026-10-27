# SARIMA guide — color and font reference

The guide defaults to dark mode. Its header button switches to the retained light theme and saves the choice when browser storage is available. It remains usable offline; print/PDF uses a light palette.

## Colors

| Role | CSS token | Dark | Light |
| --- | --- | --- | --- |
| Page background | `--paper` | `#0B1220` | `#F6F5EF` |
| Cards, inputs and tables | `--surface` | `#131F30` | `#FFFFFF` |
| Primary text / headings | `--ink` | `#E6EDF5` | `#172E3B` |
| Secondary text | `--muted` | `#A5B4C6` | `#4E6570` |
| Decorative borders / chart grid | `--line` | `#2A3B50` | `#D8E1DF` |
| Links / actual-sales line | `--blue` | `#72B7FF` | `#126DA0` |
| Past-prediction line / notices | `--orange` | `#FFB86B` | `#A7500D` |
| Future-prediction line / accents | `--green` | `#63D6B1` | `#087B61` |
| Information callout | `--soft` | `#142B3D` | `#E9F2F4` |
| Warm callout | `--gold` | `#342818` | `#FFF3DA` |
| Input / theme-button border | `--control-border` | `#71869F` | `#71869F` |
| Inline code background | `--inline-code` | `#203047` | `#E9EFEE` |
| Code block background | `--code-bg` | `#09111C` | `#122C3A` |
| Code block text | `--code-ink` | `#DCEAF5` | `#E3F2F7` |
| Table heading background | `--table-head` | `#1A2A3E` | `#E9F0F0` |
| Active navigation | `--active-bg` | `#193347` | `#E7EFEE` |
| Reading-path callout background | `--route-bg` | `#152A40` | `#172E3B` |
| Reading-path callout text | `--route-ink` | `#E6EDF5` | `#FFFFFF` |
| Reading-path callout link | `--route-link` | `#9BD4FF` | `#B0E5F4` |
| Interactive lab border | `--lab-border` | `#415972` | `#AEC8CF` |
| Calculator output background | `--output-bg` | `#142E2A` | `#EDF5F3` |
| Metric tile background | `--metric-bg` | `#101D2D` | `#F8FBFA` |
| Training / walk-forward background | `--training-bg` | `#193347` | `#DEEBED` |
| Validation background | `--validation-bg` | `#3B2D1B` | `#F9E4BC` |
| Test background | `--test-bg` | `#153A2D` | `#D5EDE2` |
| Unobserved future background | `--future-bg` | `#1B2737` | `#F0F0ED` |
| Current-period highlight | `--current-bg` | `#19352F` | `#EDF4F1` |
| Chart reference line | `--reference` | `#8FA6BC` | `#71888D` |
| Teaching connector | `--bridge` | `#C1CCDA` | `#6C757C` |
| Keyboard focus ring | `--focus` | `#FFD166` | `#B77518` |

The decorative divider color is subtle; interactive fields use the stronger control-border token. Chart strokes and walk-forward colors use the same CSS variables, so they follow theme changes and print styling.

## Fonts

| Use | CSS stack |
| --- | --- |
| Body, headings, controls and chart labels | `system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif` |
| Equations and mathematical symbols | `Georgia, "Times New Roman", serif` |
| Code, keyboard text and chapter numbers | `ui-monospace, SFMono-Regular, Consolas, monospace` |

No fonts are downloaded. These are fallback stacks: the browser uses the first available local font, so the actual rendered face varies by operating system. Body text is 17px on desktop and 16px on small screens; code blocks are 13px with a 1.8 line height, while inline code uses 0.87em. Headings scale with viewport width.

## Printing

Printing overrides all theme tokens to the light palette, uses white paper and dark text, expands explanations and preserves the pre-print theme afterwards. The original light code block styling is simplified to dark text on a light gray background for print.
