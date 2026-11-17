# GEARS Perturbation Benchmark (`gears_perturb`)

`gears_perturb` evaluates single- and multi-gene transcriptional perturbation prediction on the Norman et al. (2019) K562 Perturb-seq dataset (`split="simulation"`, `seed=42`, `default_pert_graph=False`, $G=5045$ genes, $N=103$ unique test perturbations across `unseen_single`, `combo_seen0`, `combo_seen1`, and `combo_seen2`).

## Architecture and Data Isolation

The benchmark follows a strict verified-replay boundary between public task inputs and private ground truth:

1. **Benchmark (`gears_perturb`)**: Reads `public_manifest.json` (`schema: "gears-perturb-public-v1"`), exposes only `gene_names`, `candidate_perts`, and `pert_subtypes` in `BenchmarkTask.metadata`, and sets `ground_truth=None`.
2. **Agent (`gears`)**: Supports `predictor_mode` in `("gears", "cell_mean", "train_mean")`. Verifies `task.ground_truth is None`, validates the SHA-256 of `public_manifest.json`, `registry.json` (`schema: "gears-perturb-registry-v1"`), and `predictions.npz`, and checks gene/perturbation ordering and numerical finiteness before returning the verified artifact reference.
3. **Scorer (`gears_perturb`)**: Independently verifies the pinned `private_manifest.json` (`schema: "gears-perturb-private-v1"`), `private_ground_truth.npz`, `registry.json`, and `predictions.npz` SHA-256 digests, then computes:
   - `p_delta_top20` (primary score): Mean Pearson correlation between predicted perturbation effect $\Delta^{\text{pred}}_p = \mu^{\text{pred}}_p - \mu^{\text{ctrl}}$ and true effect $\Delta^{\text{true}}_p = \mu^{\text{true}}_p - \mu^{\text{ctrl}}$ on top-20 differentially expressed (DE) genes per perturbation (explicitly defined as `0.0` when prediction delta variance is zero, e.g., `cell_mean`).
   - `all_gene_pearson`: Mean Pearson correlation between $\mu^{\text{pred}}_p$ and $\mu^{\text{true}}_p$ across all 5,045 genes.
   - `mae`: Mean absolute error across all 5,045 genes.
   - `mse_top20`: Mean squared error on the top-20 DE genes per perturbation.

## Configuration and Execution

Preset configurations are provided under `configs/gears/`:

- `configs/gears/norman_seed42_gears.yaml`
- `configs/gears/norman_seed42_cell_mean.yaml`
- `configs/gears/norman_seed42_train_mean.yaml`

Validate and execute via `bin/virtual-cell` (or `python -m alphadiana.cli`):

```bash
bin/virtual-cell validate configs/gears/norman_seed42_gears.yaml
bin/virtual-cell compute  configs/gears/norman_seed42_gears.yaml
bin/virtual-cell compute  configs/gears/norman_seed42_cell_mean.yaml
bin/virtual-cell compute  configs/gears/norman_seed42_train_mean.yaml
```

## Norman (`simulation` split, `seed=42`) Evaluation Summary

| Predictor Mode | `score_status` | `p_delta_top20` (↑) | `all_gene_pearson` (↑) | `mae` (↓) | `mse_top20` (↓) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`gears`** | `valid_scored` | **0.7075** | **0.9884** | 0.0287 | **0.1789** |
| **`train_mean`** | `valid_scored` | 0.5994 | 0.9872 | **0.0174** | 0.3410 |
| **`cell_mean`** | `valid_scored` | **0.0000** | 0.9825 | 0.0198 | 0.4592 |

### Breakdown by Perturbation Subtype (`p_delta_top20` / `mse_top20`)

| Subtype | Count | `gears` | `train_mean` | `cell_mean` |
| :--- | :---: | :---: | :---: | :---: |
| `unseen_single` | 26 | **0.5834** / **0.1752** | 0.4782 / 0.2171 | 0.0000 / 0.2874 |
| `combo_seen0` | 5 | **0.6699** / **0.0613** | 0.6057 / 0.1781 | 0.0000 / 0.2717 |
| `combo_seen1` | 55 | **0.7357** / **0.1965** | 0.6392 / 0.3890 | 0.0000 / 0.5255 |
| `combo_seen2` | 17 | **0.8173** / **0.1623** | 0.6542 / 0.4233 | 0.0000 / 0.5626 |
