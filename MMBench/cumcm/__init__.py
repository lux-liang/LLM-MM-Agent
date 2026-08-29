"""CUMCM-oriented paper benchmarking utilities.

The package deliberately keeps the reference corpus link-only.  Papers are
read from a local directory supplied by the user; no copyrighted files are
downloaded or redistributed by the evaluator.
"""

__all__ = ["evaluate_document", "normalize_report"]


def __getattr__(name: str):
    """Load the CLI module lazily so ``python -m`` stays warning-free."""

    if name in __all__:
        from importlib import import_module

        evaluate_paper = import_module(".evaluate_paper", __name__)
        return getattr(evaluate_paper, name)
    raise AttributeError(name)
