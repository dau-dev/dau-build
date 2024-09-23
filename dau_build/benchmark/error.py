from logging import getLogger
from math import sqrt
from typing import Literal, Union

import numpy as np
from qonnx.core.onnx_exec import execute_onnx
from torch import Tensor, abs, from_numpy, no_grad, stack, zeros
from torch.nn import Module

from ..finn import ModelWrapper
from .onnx import infer_model

__all__ = (
    "model_error",
    "test_model",
    "extrapolate_model",
)

log = getLogger(__name__)

ERROR_KIND = Literal["mse", "mae"]


def model_error(predictions: Tensor, test: Tensor, batch_size: int = 20, kind: ERROR_KIND = "mae"):
    if kind == "mae":
        return abs(predictions - test[batch_size:]).sum().data / len(predictions)
    elif kind == "mse":
        return sqrt(((predictions - test[batch_size:]) ** 2).sum().data / len(predictions))
    else:
        raise NotImplementedError(f"Unknown error type: {kind}")


def test_model(
    model: Union[ModelWrapper, Module],
    train_series: Tensor,
    test_series: Tensor,
    batch_size: int = 20,
    return_series: bool = False,
    error: ERROR_KIND = "mae",
) -> Union[Tensor, float]:
    """Evaluate point-for-point error against test data"""

    log.info("Testing model")

    test_dataset = [test_series[i : i + batch_size] for i in range(len(train_series) - batch_size)]
    test_dataset = stack(test_dataset).unsqueeze(0)

    with no_grad():
        if isinstance(model, Module):
            predictions = model(test_dataset).squeeze()
            print(predictions.shape)
        elif isinstance(model, ModelWrapper):
            predictions = zeros(test_dataset.shape[1])
            for i, test_data in enumerate(test_dataset[0]):
                # reshape and go to numpy
                test_data = test_data.numpy().reshape((1, batch_size))
                try:
                    predictions[i] = from_numpy(infer_model(model, test_data))
                except TypeError:
                    import pdb

                    pdb.set_trace()
                    predictions[i] = from_numpy(infer_model(model, test_data))
    if return_series:
        # return the series of predictions
        return predictions
    # calculate cumulative error
    return model_error(predictions=predictions, test=test_series, batch_size=batch_size, kind=error)


def extrapolate_model(
    model: Union[ModelWrapper, Module],
    test_series: Tensor,
    range: int = 400,
    batch_size: int = 20,
    return_series: bool = False,
):
    """Extrapolate model cumulatively, starting from first batch of test data"""

    log.info("Extrapolating model")

    extrapolation = []
    seed_batch = test_series[:batch_size].reshape(1, batch_size)
    current_batch = seed_batch
    with no_grad():
        for _ in range(range):
            input_dict = {"global_in": current_batch}
            output_dict = execute_onnx(model, input_dict)
            produced_finn = output_dict[list(output_dict.keys())[0]]
            extrapolation.append(produced_finn[0][0])

            current_batch = np.concatenate((current_batch[:, 1:], produced_finn), axis=1)
