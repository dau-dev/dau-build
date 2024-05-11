import matplotlib.colors
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
from typing import Dict

__all__ = ("plot_model",)


def plot_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    test_range: np.ndarray,
    serieses: Dict[str, np.ndarray] = None,
    to_file: str = "",
):
    fig, ax = plt.subplots(1, 1, figsize=(15, 5))
    ax.plot(X_train, y_train, lw=1, label="train data")
    ax.plot(X_test, y_test, lw=1, c="purple", label="test data")

    norm = matplotlib.colors.Normalize(vmin=0, vmax=len(serieses))

    for i, (label, series) in enumerate((serieses or {}).items()):
        ax.plot(test_range, series, lw=3, c=cm.jet(norm(i)), linestyle=":", label=label)

    ax.legend(loc="lower left")
    if to_file:
        plt.savefig(to_file)
    else:
        plt.show()
