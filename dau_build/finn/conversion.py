from finn.transformation.qonnx.convert_qonnx_to_finn import ConvertQONNXtoFINN


def convert_to_qonnx(model):
    model = model.transform(ConvertQONNXtoFINN())
    return model
