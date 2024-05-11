import numpy as np
from logging import getLogger
from torch import Tensor

from .error import model_error, test_model
from .graph import plot_model

log = getLogger(__name__)

__all__ = ("run_all_benchmarks",)


def run_all_benchmarks(
    model,
    # for testing
    train_series: Tensor,
    test_series: Tensor,
    batch_size: int,
    # for plotting
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    to_file: str,
    # extras
    label: str = "prediction",
):
    prediction_series = test_model(model, test_series=test_series, train_series=train_series, batch_size=batch_size, return_series=True)
    log.info(f"Model error: {model_error(prediction_series, test_series):.4f}")
    log.info(f"Writing graph to: {to_file}")
    plot_model(
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        test_range=X_test[batch_size:],
        serieses={label: prediction_series},
        to_file=to_file,
    )
