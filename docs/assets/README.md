# README figure sources

The README paper images are rendered from the sources supplied in
`AlphaDiana-draft-main.zip` on 2026-10-12. They follow the PDF files referenced by
`0-shared/sections/main.tex`, rather than similarly named legacy PNG files.

| Repository asset | Paper source | Figure |
| --- | --- | --- |
| `alphadiana-motivation.png` | `0-shared/figures/AlphaDiana-motivation.pdf` | 1: Position of AlphaDiana; follows the scope table |
| `alphadiana-overview.png` | `0-shared/figures/AlphaDiana-Fig2.pdf` | 2: Overview; measurement examples in Why AlphaDiana |
| `alphadiana-system.png` | `0-shared/figures/AlphaDiana-system.pdf` | 3: System design |
| `alphadiana-interface.png` | `0-shared/figures/AlphaDiana-interface.pdf` | 4: Functional interface; reproduced unchanged |
| `alphadiana-action-patterns.png` | `0-shared/figures/revised_figures/fig_action_four_panels_final.pdf` | Functional-step patterns; results and analysis |
| `alphadiana-resource-use.png` | `0-shared/figures/efficiency/AlphaDiana-macro-efficiency.pdf` | Output tokens and wall-clock time; results and analysis |

The overview, positioning, system design and functional interface figures are
all visible by default. The action-pattern and
resource-use assets remain available for results documentation.

Each one-page PDF was rendered with PDFium at scale 2.2 or higher for legibility, without changing its
content. The overview includes the draft's experimental values; these are not
claims about the README Quick Start or a new validation run. The system figure
shows the conceptual architecture. Runtime and evidence support depend on the
configured adapter and provider.

Source PDF SHA-256 checksums:

- `AlphaDiana-interface.pdf`: `7c6747b69599af4f047e3906cd8e614185e48e2df031bd0859e2bc71511ce093`
- `AlphaDiana-motivation.pdf`: `461c645600e2030e3e0610d9fb8bfa5c5cefc48cfa8f821954b30830f5156382`
- `AlphaDiana-Fig2.pdf`: `284d87304149e5a2c5b01cd471a54aeac00dad12c2c61f3cbb03a022ce73c2f6`
- `AlphaDiana-system.pdf`: `9f52ab04ca837d5ecdace3a62b3ff2649bf09672dc461d9ed2ac05660595ba54`
- `fig_action_four_panels_final.pdf`: `2d3da054c1391d1ef07ecafa7619088cbb65e7a7b446a657b42a8371023f3921`
- `AlphaDiana-macro-efficiency.pdf`: `e0046f848942caf28a228997da787d542b14006dbc160bd04977f1de2d5e9c63`

Figure 4 (`AlphaDiana-interface.pdf`) is reproduced unchanged in the Python API
section as a manuscript illustration, not an executable guide:
the supplied illustration uses `ad.scorer(...)` and a string-valued runner
environment, while the current API uses `ad.scorer.create(...)` and an
`EnvironmentSpec`. The README and Python API guide retain the current examples.

Dashboard screenshots remain separate UI illustrations and are unchanged by
this draft update.
