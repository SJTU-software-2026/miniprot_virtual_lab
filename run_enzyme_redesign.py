#!/usr/bin/env python
"""
Enzyme Redesign Pipeline — Command-Line Interface

基于 Meng et al. (2021) ACS Catalysis 的计算酶改造自动化流程。

用法示例:
    python run_enzyme_redesign.py \
        --enzyme 6TB1.pdb \
        --ligand substrate.sdf \
        --work-dir output \
        --large-pocket 54 57 58 151 230 261 417 \
        --small-pocket 86 \
        --strategy smart \
        --top-n 20

    # 通用酶（无需指定大/小口袋，自动全部检测）
    python run_enzyme_redesign.py \
        --enzyme my_enzyme.pdb \
        --ligand my_ligand.sdf \
        --work-dir output

    # PLP转氨酶模式（论文同款）
    python run_enzyme_redesign.py \
        --enzyme 6TB1.pdb \
        --ligand amine_product.sdf \
        --work-dir output \
        --plp PLP \
        --external-aldimine \
        --large-pocket 54 57 58 151 230 261 417 \
        --small-pocket 86
"""
import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src" / "miniprot_virtual_lab"))

from workflows.enzyme_redesign.pipeline import EnzymeRedesignPipeline


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enzyme Redesign Pipeline — Computational enzyme engineering",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 通用酶改造
  python run_enzyme_redesign.py -e enzyme.pdb -l ligand.sdf -w output/

  # PjTA-R6 转氨酶改造（论文模式）
  python run_enzyme_redesign.py -e 6TB1.pdb -l substrate.sdf -w output/ \\
      --large-pocket 54 57 58 151 230 261 417 --small-pocket 86 --plp PLP

  # 指定需要突变的位置
  python run_enzyme_redesign.py -e enzyme.pdb -l ligand.sdf -w output/ \\
      --target-positions 54 58 86 151

更多信息: https://doi.org/10.1021/acscatal.1c02053
        """,
    )

    # ── 必选参数 ──
    parser.add_argument(
        "-e", "--enzyme",
        required=True,
        type=str,
        help="输入酶结构 PDB 文件路径",
    )
    parser.add_argument(
        "-l", "--ligand",
        required=True,
        type=str,
        help="输入配体 SDF 或 PDB 文件路径",
    )
    parser.add_argument(
        "-w", "--work-dir",
        required=True,
        type=str,
        help="输出工作目录路径",
    )

    # ── 口袋参数 ──
    parser.add_argument(
        "--plp",
        type=str,
        default="PLP",
        help="PLP辅因子在PDB中的残基名（默认: PLP）",
    )
    parser.add_argument(
        "--large-pocket",
        type=int,
        nargs="*",
        default=None,
        help="大口袋残基编号列表，如: 54 57 58 151 230 261 417",
    )
    parser.add_argument(
        "--small-pocket",
        type=int,
        nargs="*",
        default=None,
        help="小口袋残基编号列表，如: 86",
    )
    parser.add_argument(
        "--pocket-cutoff",
        type=float,
        default=8.0,
        help="口袋检测距离阈值 (A)，默认 8.0",
    )

    # ── 突变设计参数 ──
    parser.add_argument(
        "--strategy",
        type=str,
        choices=["smart", "full"],
        default="smart",
        help="突变策略: smart=curated AA subsets（推荐）, full=全19种氨基酸扫描",
    )
    parser.add_argument(
        "--target-positions",
        type=int,
        nargs="*",
        default=None,
        help="指定要突变的具体残基编号（不指定则用全部口袋残基）",
    )

    # ── 对接参数 ──
    parser.add_argument(
        "-n", "--top-n",
        type=int,
        default=20,
        help="建模和对接的突变体数量上限（默认 20）",
    )
    parser.add_argument(
        "--exhaustiveness",
        type=int,
        default=8,
        help="Vina 对接 thoroughness（默认 8, 越大越精确但越慢）",
    )
    parser.add_argument(
        "--box-padding",
        type=float,
        default=5.0,
        help="对接盒子 padding (A)，默认 5.0",
    )

    # ── 外部醛亚胺模式 ──
    parser.add_argument(
        "--external-aldimine",
        action="store_true",
        help="构建 PLP-底物外部醛亚胺中间体（转氨酶论文模式）",
    )

    # ── 高级参数 ──
    parser.add_argument(
        "--scwrl",
        type=str,
        default="Scwrl4",
        help="SCWRL4 可执行文件路径或名称（默认: Scwrl4）",
    )
    parser.add_argument(
        "--vina",
        type=str,
        default="vina",
        help="AutoDock Vina 可执行文件路径或名称（默认: vina）",
    )
    parser.add_argument(
        "--obabel",
        type=str,
        default="obabel",
        help="OpenBabel 可执行文件路径或名称（默认: obabel）",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # 验证输入文件存在
    enzyme_pdb = Path(args.enzyme)
    ligand_sdf = Path(args.ligand)
    work_dir = Path(args.work_dir)

    if not enzyme_pdb.exists():
        print(f"[ERROR] Enzyme PDB not found: {enzyme_pdb}")
        sys.exit(1)
    if not ligand_sdf.exists():
        print(f"[ERROR] Ligand file not found: {ligand_sdf}")
        sys.exit(1)

    # 显示配置
    print("\n" + "=" * 60)
    print("  Enzyme Redesign Pipeline")
    print("  Meng et al. (2021) — SCWRL4 + Vina toolchain")
    print("=" * 60)
    print(f"  Enzyme:        {enzyme_pdb}")
    print(f"  Ligand:        {ligand_sdf}")
    print(f"  Work dir:      {work_dir}")
    print(f"  PLP resname:   {args.plp}")
    print(f"  Large pocket:  {args.large_pocket or 'auto-detect'}")
    print(f"  Small pocket:  {args.small_pocket or 'auto-detect'}")
    print(f"  Strategy:      {args.strategy}")
    print(f"  Top N:         {args.top_n}")
    print(f"  Exhaustiveness:{args.exhaustiveness}")
    print(f"  Aldimine mode: {args.external_aldimine}")
    print("=" * 60)

    # 运行 pipeline
    pipeline = EnzymeRedesignPipeline(
        enzyme_pdb=enzyme_pdb,
        ligand_sdf=ligand_sdf,
        work_dir=work_dir,
        plp_resname=args.plp,
        large_pocket_residues=args.large_pocket,
        small_pocket_residues=args.small_pocket,
        pocket_cutoff=args.pocket_cutoff,
        mutation_strategy=args.strategy,
        target_positions=args.target_positions,
        top_n=args.top_n,
        exhaustiveness=args.exhaustiveness,
        box_padding=args.box_padding,
        build_external_aldimine=args.external_aldimine,
        scwrl_binary=args.scwrl,
        vina_binary=args.vina,
        obabel_binary=args.obabel,
    )

    try:
        state = pipeline.run()
    except Exception as e:
        print(f"\n[FATAL] Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if state.failed:
        print(f"\n[FAILED] {state.error_message}")
        sys.exit(1)

    # 输出最终结果
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    if state.summary_csv:
        print(f"  Full report: {state.summary_csv}")
    if state.ranked_results:
        print(f"\n  Top {min(5, len(state.ranked_results))} recommendations:")
        for r in state.ranked_results[:5]:
            print(f"    #{r['rank']} {r['mutant']}: "
                  f"{r['vina_score']:.2f} kcal/mol "
                  f"(position {r['position']}, {r['wt_aa']}->{r['mut_aa']}, "
                  f"{r['design_group']})")
    print("=" * 60)


if __name__ == "__main__":
    main()
