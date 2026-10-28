"""Editable script: plot scalar batch outputs without replaying any receiver I/Q."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="Run directory containing results.jsonl")
    parser.add_argument("--output", type=Path, help="Save an image instead of opening a GUI")
    args = parser.parse_args()
    import matplotlib
    if args.output:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from rf_skill_harness.plotting import plot_results
    fig = plot_results(args.results)
    if args.output:
        fig.savefig(args.output, dpi=150)
    else:
        plt.show()


if __name__ == "__main__":
    main()
