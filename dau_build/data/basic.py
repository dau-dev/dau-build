from functools import lru_cache
from typing import Tuple

import numpy as np
import torch
from torch import Tensor

__all__ = (
    "simple_positive_sine",
    "simple_positive_sine_series",
    "simple_positive_sine_training_batches",
)


@lru_cache
def simple_positive_sine(mult: float = 16, astype: type = np.float32) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    X_train = np.arange(0, 100, 0.5).astype(astype)
    y_train = ((np.sin(X_train) + 1.0) * mult).astype(astype)
    X_test = np.arange(100, 200, 0.5).astype(astype)
    y_test = ((np.sin(X_test) + 1.0) * mult).astype(astype)
    return X_train, y_train, X_test, y_test


@lru_cache
def simple_positive_sine_series(astype: type = torch.float32) -> Tuple[Tensor, Tensor]:
    _, y_train, _, y_test = simple_positive_sine()
    train_series = torch.from_numpy(y_train).type(astype)
    test_series = torch.from_numpy(y_test).type(astype)
    return train_series, test_series


@lru_cache
def simple_positive_sine_training_batches(batch_size: int = 20):
    train_series, _ = simple_positive_sine_series()

    train_dataset = []
    train_labels = []
    for i in range(len(train_series) - batch_size):
        train_dataset.append(train_series[i : i + 20])
        train_labels.append(train_series[i + 20])

    train_dataset = torch.stack(train_dataset).unsqueeze(0)
    train_labels = torch.stack(train_labels).unsqueeze(0).unsqueeze(2)
    return train_dataset, train_labels
