from finn.transformation.fpgadataflow.convert_to_hw_layers import (
    InferAddStreamsLayer,
    InferBinaryMatrixVectorActivation,
    InferChannelwiseLinearLayer,
    InferConcatLayer,
    InferConvInpGen,
    InferDataTypes,
    InferDuplicateStreamsLayer,
    InferGlobalAccPoolLayer,
    InferLabelSelectLayer,
    InferLookupLayer,
    InferPool,
    InferQuantizedMatrixVectorActivation,
    InferShapes,
    InferStreamingEltwise,
    InferStreamingMaxPool,
    InferThresholdingLayer,
    InferUpsample,
    InferVectorVectorActivation,
)
from logging import getLogger

__all__ = (
    "InferConcatLayer",
    "InferAddStreamsLayer",
    "InferBinaryMatrixVectorActivation",
    "InferChannelwiseLinearLayer",
    "InferConvInpGen",
    "InferDataTypes",
    "InferDuplicateStreamsLayer",
    "InferGlobalAccPoolLayer",
    "InferLabelSelectLayer",
    "InferLookupLayer",
    "InferPool",
    "InferQuantizedMatrixVectorActivation",
    "InferShapes",
    "InferStreamingEltwise",
    "InferStreamingMaxPool",
    "InferThresholdingLayer",
    "InferUpsample",
    "InferVectorVectorActivation",
    "convert_to_hw",
)

log = getLogger(__name__)


def convert_to_hw(model):
    for t in (
        InferQuantizedMatrixVectorActivation,
        InferThresholdingLayer,
        InferBinaryMatrixVectorActivation,
    ):
        log.info(f"Transforming model with {t.__name__}")
        model = model.transform(t())
    return model
