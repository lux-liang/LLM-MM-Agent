"""Reproducible Q1/Q2 writing and visualization support for CUMCM 2026 B.

The module deliberately separates three concerns:

* numeric evidence is loaded and checked without calling an LLM;
* a constrained editor prompt is assembled from that evidence; and
* small, deterministic vector figures are rendered for the TeX manuscript.

It is an integration helper for the MM-Agent repository.  It does not infer
hidden simulator state, call the official interface, or write credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Matplotlib is optional for the text/evidence audit.  Keeping it lazy lets the
# deterministic evaluator tests run in a minimal CI environment; only the
# figure command requires the plotting extra.
matplotlib = None
plt = None
Circle = None
Polygon = None


def _ensure_plotting() -> None:
    global matplotlib, plt, Circle, Polygon
    if plt is not None:
        return
    try:
        import matplotlib as mpl
        mpl.use("Agg")
        from matplotlib import pyplot as pyplot
        from matplotlib.patches import Circle as circle, Polygon as polygon
    except ImportError as exc:
        raise RuntimeError(
            "render_q1_q2_figures requires matplotlib; install the plotting extra"
        ) from exc
    matplotlib = mpl
    plt = pyplot
    Circle = circle
    Polygon = polygon


REQUIRED_TOP_LEVEL = {"metadata", "q1", "q2_selected", "q2_baselines", "sensitivity"}


def _reject_nonfinite(value: Any, path: str = "$") -> None:
    """Reject NaN/Inf anywhere in a JSON-like evidence object."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"non-finite numeric value at {path}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_nonfinite(child, f"{path}.{key}")
        return
    raise ValueError(f"unsupported evidence value at {path}: {type(value).__name__}")


def load_q1_q2_results(path: str | Path) -> dict[str, Any]:
    """Load and validate the canonical Q1/Q2 result artifact.

    ``json.loads`` is configured to reject non-standard constants so that a
    silently serialized NaN cannot enter a paper or a chart.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)

    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON constant {token!r} in {source}")

    try:
        data = json.loads(source.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON evidence: {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Q1/Q2 evidence root must be an object")
    missing = sorted(REQUIRED_TOP_LEVEL.difference(data))
    if missing:
        raise ValueError(f"Q1/Q2 evidence is missing top-level keys: {', '.join(missing)}")
    if not isinstance(data["q2_baselines"], list) or not data["q2_baselines"]:
        raise ValueError("q2_baselines must be a non-empty list")
    _reject_nonfinite(data)
    return data


def _selected_bounds(results: Mapping[str, Any]) -> tuple[float, float]:
    selected = results["q2_selected"]
    return float(selected["pair_lower_radius_m"]), float(selected["pair_upper_radius_m"])


def _baseline(results: Mapping[str, Any]) -> Mapping[str, Any]:
    for item in results["q2_baselines"]:
        if item.get("candidate_family") == "current_grid":
            return item
    return results["q2_baselines"][0]


def build_q1_q2_editor_prompt(results: Mapping[str, Any]) -> str:
    """Build a constrained, evidence-first prompt for the MM-Agent writer."""
    selected_lower, selected_upper = _selected_bounds(results)
    baseline = _baseline(results)
    baseline_lower = float(baseline["robust"]["lower_radius_m"])
    baseline_upper = float(baseline["robust"]["upper_radius_m"])
    q1 = results["q1"]
    q1_quad = q1["symmetric_quadrilateral"]
    q1_tri = q1["equilateral_triangle"]
    improvement = results["q2_selected"]["improvement_vs_current_grid"]
    return f"""你是 CUMCM 2026 B 题的严格数学建模论文编辑。请只输出可直接放入 TeX 的正文，不输出 Markdown、解释或未经证据支持的数字。

写作目标：把问题一和问题二写成一条可复核的论证链。每一问依次包含：一句话核心、数学归类、必要假设、建模思想、算法求解、模型优点与限制、检验/灵敏度、量化结论和推广应用。

证据锁定：
 * 问题一对称四边形：D={float(q1_quad['diameter_m']):.6f} m，MEC 半径={float(q1_quad['mec_radius_m']):.6f} m，覆盖判定为真。
 * 问题一等边三角形反例：D={float(q1_tri['diameter_m']):.6f} m，MEC 半径={float(q1_tri['mec_radius_m']):.6f} m，直径圆判定为假。
* 问题二当前网格鲁棒区间=[{baseline_lower:.6f},{baseline_upper:.6f}] m。
* 问题二所选镜像鲁棒区间=[{selected_lower:.6f},{selected_upper:.6f}] m。
* 区间改进范围=[{float(improvement['guaranteed_percent']):.4f},{float(improvement['possible_percent']):.4f}]%。

模型分工必须保持：主模型是确定性有界误差下的连续未来示向度 minimax，并用内层区间上下界证书；辅模型是局部条带面积、等效半径和 CRLB，只解释几何趋势。辅模型不能替代官方确定性判定，也不能把有限外层搜索写成连续全局最优。必须明确说明“不能把有限外层搜索写成连续全局最优”，并保留 18 个区间重叠的近邻候选这一限制。

请保留以下批判性要点：D≤40 m 只是必要条件，最终要检查 MEC≤20 m；接收半径下端随源位置为 max(1000,||s-g||)；near 与 direction 反馈分开；量化误差采用 1.005° 仅是数值保守处理，不是高斯标准差；所有结果需注明是离线确定性证据，不是官方成绩。
"""


def _read_texts(paths: Iterable[str | Path]) -> str:
    chunks = []
    for path in paths:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        chunks.append(source.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def audit_q1_q2_latex(paths: Sequence[str | Path], results: Mapping[str, Any]) -> dict[str, Any]:
    """Check that the manuscript states the main claims and caveats."""
    text = _read_texts(paths)
    selected_lower, selected_upper = _selected_bounds(results)
    baseline = _baseline(results)
    improvement = results["q2_selected"]["improvement_vs_current_grid"]
    required: dict[str, tuple[str, ...]] = {
        "q1_section": ("sec:b12-q1",),
        "q2_section": ("sec:b12-q2",),
        "mec_certificate": ("b12-mec", "最小包围圆"),
        "diameter_counterexample": ("等边三角形", "直径圆"),
        "safe_domain": ("b12-lens", "保证接收域"),
        "main_auxiliary_split": ("主模型", "辅模型"),
        "finite_search_limit": ("不能宣称连续全局最优", "有限外层"),
        "selected_radius": (f"{selected_lower:.6f}",),
        "baseline_radius": (f"{float(baseline['robust']['lower_radius_m']):.6f}",),
        "improvement_interval": (f"{float(improvement['guaranteed_percent']):.4f}",),
        "error_sensitivity": ("1.005", "灵敏度"),
    }
    missing = [name for name, alternatives in required.items()
               if not any(candidate in text for candidate in alternatives)]
    return {
        "passed": not missing,
        "missing": missing,
        "matched": len(required) - len(missing),
        "required_claims": list(required),
        # Keep reports portable and avoid leaking a contributor's local
        # directory.  The caller still reads the exact paths above; the
        # manifest only needs stable file names for audit provenance.
        "files": [Path(path).name for path in paths],
        "selected_interval_m": [selected_lower, selected_upper],
    }


def _write_figure(fig: Any, output_dir: Path, stem: str, caption: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{stem}.pdf"
    png_path = output_dir / f"{stem}.png"
    # Matplotlib otherwise inserts the current time into every PDF.  Removing
    # both date fields makes the manifest hashes meaningful: identical input
    # evidence produces byte-identical vector figures across runs.
    metadata = {
        "Creator": "LLM-MM-Agent CUMCM Q1/Q2 pipeline",
        "Title": caption,
        "CreationDate": None,
        "ModDate": None,
    }
    fig.savefig(pdf_path, format="pdf", metadata=metadata, bbox_inches="tight")
    fig.savefig(png_path, format="png", dpi=220, metadata={"Software": "LLM-MM-Agent"})
    plt.close(fig)
    return {
        "stem": stem,
        "pdf": pdf_path.name,
        "png": png_path.name,
        "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "png_sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
        "caption": caption,
        "vector_pdf": True,
    }


def _figure_q1(results: Mapping[str, Any]) -> Any:
    tri = results["q1"]["equilateral_triangle"]
    vertices = tri["vertices_m"]
    mec_center = tri["mec_center_m"]
    mec_radius = float(tri["mec_radius_m"])
    fig, ax = plt.subplots(figsize=(6.0, 4.8))
    ax.set_aspect("equal")
    ax.add_patch(Polygon(vertices, closed=True, facecolor="#e8eef7", edgecolor="#1f3b5b", linewidth=1.4))
    ax.add_patch(Circle((20.0, 0.0), 20.0, fill=False, linestyle="--", color="#b5423d", linewidth=1.3,
                        label="diameter circle (r=20 m)"))
    ax.add_patch(Circle(mec_center, mec_radius, fill=False, color="#2e7d5b", linewidth=1.3,
                        label="MEC (r=23.094 m)"))
    xs, ys = zip(*vertices)
    ax.scatter(xs, ys, color="#1f3b5b", s=22, zorder=3)
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.set_title("Q1: diameter circle versus minimum enclosing circle")
    ax.grid(alpha=0.22)
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    return fig


def _figure_q2_interval(results: Mapping[str, Any]) -> Any:
    baseline = _baseline(results)
    selected = results["q2_selected"]
    rows = [("current grid", baseline["robust"]),
            ("right-angle anchor", next(x["robust"] for x in results["q2_baselines"]
                                         if x.get("candidate_family") == "safe_local_right_angle_anchor")),
            ("analytic anchor", next(x["robust"] for x in results["q2_baselines"]
                                      if x.get("candidate_family") == "analytic_anchor")),
            ("selected mirror", selected["mirror_equivalent_candidates"][0]["robust"])]
    labels = [x[0] for x in rows]
    lows = [float(x[1]["lower_radius_m"]) for x in rows]
    highs = [float(x[1]["upper_radius_m"]) for x in rows]
    centers = [(a + b) / 2 for a, b in zip(lows, highs)]
    errors = [[c - a for c, a in zip(centers, lows)], [b - c for b, c in zip(highs, centers)]]
    fig, ax = plt.subplots(figsize=(7.0, 4.3))
    y = list(range(len(labels)))
    ax.errorbar(centers, y, xerr=errors, fmt="o", capsize=4, color="#245b8a", ecolor="#245b8a", linewidth=1.4)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("certified worst-case MEC radius (m)")
    ax.set_title("Q2: robust interval comparison")
    ax.grid(axis="x", alpha=0.25)
    for yi, center in zip(y, centers):
        ax.text(center + 0.35, yi, f"{center:.2f}", va="center", fontsize=8)
    fig.tight_layout()
    return fig


def _figure_q2_sensitivity(results: Mapping[str, Any]) -> Any:
    entries = []
    sensitivity = results["sensitivity"]
    if isinstance(sensitivity, dict):
        for item in sensitivity.get("angle_error", []):
            mirrors = item.get("mirror_equivalent_candidates", [])
            if mirrors:
                robust = mirrors[0].get("robust", {})
                if "lower_radius_m" in robust and "upper_radius_m" in robust:
                    entries.append((f"eps={float(item['error_deg']):.3f} deg",
                                    float(robust["lower_radius_m"]),
                                    float(robust["upper_radius_m"])))
        for item in sensitivity.get("circle_polygonization", []):
            mirrors = item.get("mirror_equivalent_candidates", [])
            if mirrors:
                robust = mirrors[0].get("robust", {})
                if "lower_radius_m" in robust and "upper_radius_m" in robust:
                    entries.append((f"sides={int(item['circle_outer_sides_count'])}",
                                    float(robust["lower_radius_m"]),
                                    float(robust["upper_radius_m"])))
        for item in sensitivity.get("local_refinement", []):
            if isinstance(item, dict) and "lower_radius_m" in item and "upper_radius_m" in item:
                entries.append((str(item.get("label", "refinement")),
                                float(item["lower_radius_m"]), float(item["upper_radius_m"])))
    elif isinstance(sensitivity, list):
        for item in sensitivity:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("setting") or item.get("name")
            robust = item.get("robust") or item
            if isinstance(robust, dict) and "lower_radius_m" in robust and "upper_radius_m" in robust:
                entries.append((str(label), float(robust["lower_radius_m"]), float(robust["upper_radius_m"])))
    if not entries:
        # Keep the figure useful if a future artifact changes its nested label,
        # while still making the absence visible in the manifest.
        selected_lower, selected_upper = _selected_bounds(results)
        entries = [("selected", selected_lower, selected_upper)]
    labels = [x[0] for x in entries]
    y = list(range(len(entries)))
    lows = [x[1] for x in entries]
    highs = [x[2] for x in entries]
    centers = [(a + b) / 2 for a, b in zip(lows, highs)]
    errors = [[c - a for c, a in zip(centers, lows)], [b - c for b, c in zip(highs, centers)]]
    fig, ax = plt.subplots(figsize=(7.0, max(3.2, 0.48 * len(entries) + 1.4)))
    ax.errorbar(centers, y, xerr=errors, fmt="o", capsize=3.5, color="#7a4e00", ecolor="#7a4e00", linewidth=1.2)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("certified radius interval (m)")
    ax.set_title("Q2: error and discretization sensitivity")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    return fig


def render_q1_q2_figures(results: Mapping[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Render three reproducible PDF/PNG figures and return their manifest."""
    _ensure_plotting()
    output = Path(output_dir)
    plt.rcParams.update({"font.size": 9.5, "axes.unicode_minus": False,
                         "pdf.fonttype": 42, "ps.fonttype": 42})
    figures = [
        _write_figure(_figure_q1(results), output, "q1_mec_diameter",
                      "Q1 diameter circle and minimum enclosing circle"),
        _write_figure(_figure_q2_interval(results), output, "q2_robust_intervals",
                      "Q2 certified robust interval comparison"),
        _write_figure(_figure_q2_sensitivity(results), output, "q2_sensitivity",
                      "Q2 error and discretization sensitivity"),
    ]
    return {
        "schema_version": "1.0",
        "generator": "MM-Agent/CUMCM-Q1-Q2",
        "figure_count": len(figures),
        "figures": figures,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--q1-tex", type=Path, action="append", required=True,
                        help="TeX file to include in the claim audit; repeat for validation files")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--prompt", type=Path)
    args = parser.parse_args(argv)
    results = load_q1_q2_results(args.results)
    audit = audit_q1_q2_latex(args.q1_tex, results)
    figures = render_q1_q2_figures(results, args.output)
    manifest = {"audit": audit, "figures": figures,
                "results_sha256": hashlib.sha256(args.results.read_bytes()).hexdigest()}
    if args.manifest:
        _write_json(args.manifest, manifest)
    if args.prompt:
        args.prompt.parent.mkdir(parents=True, exist_ok=True)
        args.prompt.write_text(build_q1_q2_editor_prompt(results), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
