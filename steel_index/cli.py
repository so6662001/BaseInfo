"""命令行入口：

    # 用合成数据跑一遍完整流程（含算法有效性自检）
    python -m steel_index demo --outdir out

    # 计算真实数据
    python -m steel_index run --listings 挂牌价.csv --inventory 库存.csv --outdir out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as cfg
from .diagnostics import credibility_report, format_diagnostics
from .pipeline import SteelIndexOutput, run_index
from .simulate import generate_market


def _read_table(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"文件不存在: {p}")
    if p.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(p)
    if p.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(p)
    return pd.read_csv(p)


def _dump(out: SteelIndexOutput, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    out.composite.to_csv(outdir / "composite_index.csv", index=False,
                         encoding="utf-8-sig")
    out.variety.to_csv(outdir / "variety_index.csv", index=False, encoding="utf-8-sig")
    out.indices.cells.to_csv(outdir / "cell_detail.csv", index=False,
                             encoding="utf-8-sig")
    out.indices.contribution.to_csv(outdir / "category_contribution.csv", index=False,
                                    encoding="utf-8-sig")
    out.cleaning.rejected.to_csv(outdir / "rejected_records.csv", index=False,
                                 encoding="utf-8-sig")
    out.cleaning.report.to_csv(outdir / "cleaning_report.csv", index=False,
                               encoding="utf-8-sig")
    out.credibility.scores.to_csv(outdir / "merchant_credibility.csv", index=False,
                                  encoding="utf-8-sig")
    out.diagnostics.to_csv(outdir / "daily_diagnostics.csv", index=False,
                           encoding="utf-8-sig")
    print(f"\n结果已写出到 {outdir.resolve()}")


def _build_config(args: argparse.Namespace) -> cfg.SteelIndexConfig:
    base = cfg.SteelIndexConfig()
    index_kwargs = {}
    if args.base_date:
        index_kwargs["base_date"] = args.base_date
    if args.weight_mode:
        index_kwargs["category_weight_mode"] = args.weight_mode
    if args.bootstrap:
        index_kwargs["bootstrap_rounds"] = args.bootstrap
    if index_kwargs:
        from dataclasses import replace
        base = base.with_overrides(index=replace(base.index, **index_kwargs))
    return base


def cmd_run(args: argparse.Namespace) -> int:
    listings = _read_table(args.listings)
    inventory = _read_table(args.inventory) if args.inventory else None
    out = run_index(listings, inventory, _build_config(args))
    print(out.summary())
    print()
    print(format_diagnostics(out.diagnostics))
    rep = credibility_report(out.credibility)
    if not rep.empty:
        print("\n最可疑商家（可信度最低，已降权/拉黑）")
        print(rep.to_string(index=False))
    if args.outdir:
        _dump(out, Path(args.outdir))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    market = generate_market(n_days=args.days, n_merchants=args.merchants,
                             seed=args.seed, dirty=not args.clean)
    print(f"合成市场: {len(market.listings):,} 条原始挂牌记录，"
          f"{market.meta['n_merchants']} 家商家，{market.meta['n_days']} 个交易日，"
          f"注入脏数据={market.meta['dirty']}")

    out = run_index(market.listings, config=_build_config(args))
    print()
    print(out.summary())
    print()
    print(format_diagnostics(out.diagnostics))

    rep = credibility_report(out.credibility)
    if not rep.empty:
        print("\n最可疑商家（可信度最低）")
        print(rep.to_string(index=False))

    _print_accuracy(market, out)

    if args.outdir:
        _dump(out, Path(args.outdir))
    return 0


def _print_accuracy(market, out: SteelIndexOutput) -> None:
    """把算出来的指数与合成数据的真值对比，量化算法有效性。"""
    truth = market.truth_composite()
    comp = out.composite[["date", "index_value"]].merge(truth, on="date", how="inner")
    if comp.empty:
        return
    err = comp["index_value"] - comp["true_index"]
    corr = comp["index_value"].corr(comp["true_index"])
    ret_corr = (
        comp["index_value"].pct_change().corr(comp["true_index"].pct_change())
    )
    print("\n算法有效性自检（合成数据真值对比）")
    print(f"  指数水平相关系数 : {corr:.4f}")
    print(f"  日涨跌相关系数   : {ret_corr:.4f}")
    print(f"  平均绝对误差     : {err.abs().mean():.3f} 点"
          f"（相对真值 {err.abs().mean() / comp['true_index'].mean():.3%}）")
    print(f"  末期偏差         : {err.iloc[-1]:+.3f} 点")

    detected = set(out.credibility.scores.loc[
        out.credibility.scores["blacklisted"], "merchant_id"].unique())
    truth_bad = set(market.dirty_merchants)
    normal = set(market.normal_merchants)
    if truth_bad:
        recall = len(detected & truth_bad) / len(truth_bad)
        print(f"  捣乱商家识别     : 召回 {recall:.1%}"
              f"（真实捣乱 {len(truth_bad)} 家，共拉黑 {len(detected)} 家）")
    if normal:
        fp = len(detected & normal) / len(normal)
        print(f"  正常商家误伤     : {fp:.1%}"
              f"（{len(detected & normal)} / {len(normal)} 家）")

    clones = out.credibility.clones
    truth_clone = set(market.clone_merchants)
    found_clone = set(clones["merchant_id"]) if not clones.empty else set()
    if truth_clone:
        wrong = len(found_clone - truth_clone)
        print(f"  同源小号识别     : 命中 {len(found_clone & truth_clone)} 家"
              f"（真实小号 {len(truth_clone)} 家，其中成组的才会同步报价）"
              f"，误判 {wrong} 家")

    repaired = int(out.cleaning.stats.get("repaired_rows", 0))
    unit_bad = set(market.merchants.loc[
        market.merchants["type"] == "unit_error", "merchant_id"])
    if unit_bad:
        kept = out.weighted["merchant_id"].isin(unit_bad).sum()
        print(f"  量纲错误修复     : 修复 {repaired:,} 条，"
              f"{len(unit_bad)} 家单位填错的商家有 {kept:,} 条报价被救回并正常参与指数")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="steel_index", description="钢铁行情指数算法")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--outdir", default=None, help="结果输出目录")
    common.add_argument("--base-date", default=None, help="指数基期 YYYY-MM-DD")
    common.add_argument("--weight-mode", default=None,
                        choices=["fixed", "market_value", "hybrid"],
                        help="品种权重模式")
    common.add_argument("--bootstrap", type=int, default=0,
                        help="Bootstrap 置信区间抽样次数（0 关闭）")

    run = sub.add_parser("run", parents=[common], help="计算真实数据")
    run.add_argument("--listings", required=True, help="挂牌价明细 csv/xlsx/parquet")
    run.add_argument("--inventory", default=None, help="库存明细（可选）")
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", parents=[common], help="用合成数据演示与自检")
    demo.add_argument("--days", type=int, default=180)
    demo.add_argument("--merchants", type=int, default=120)
    demo.add_argument("--seed", type=int, default=20260824)
    demo.add_argument("--clean", action="store_true", help="不注入脏数据")
    demo.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
