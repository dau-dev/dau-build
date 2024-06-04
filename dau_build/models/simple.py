from brevitas.nn import QuantIdentity, QuantLinear
from brevitas.quant import Int8WeightPerTensorFixedPoint
from logging import getLogger
from torch import Tensor
from torch.nn import Module, MSELoss
from torch.optim import Adam
from tqdm import tqdm

__all__ = (
    "QuantNet",
    "simple_model",
    "basic_training",
)

log = getLogger(__name__)


class QuantNet(Module):
    def __init__(self, n_neurons, input_shape):
        super(QuantNet, self).__init__()
        self.quant_inp = QuantIdentity(bit_width=8, return_quant_tensor=True)
        self.fc1 = QuantLinear(
            input_shape,
            n_neurons,
            weight_quant=Int8WeightPerTensorFixedPoint,
            return_quant_tensor=True,
            bias=False,
        )
        # self.relu1 = qnn.QuantReLU(bit_width=4, return_quant_tensor=True)
        self.fc2 = QuantLinear(n_neurons, 1, weight_quant=Int8WeightPerTensorFixedPoint, bias=False)

    def forward(self, x):
        out = self.quant_inp(x)
        out = self.fc1(out)
        # out = self.relu1(out)
        out = self.fc2(out)
        return out


def simple_model(n_neurons: int = 4, look_back: int = 20) -> QuantNet:
    log.info(f"Building simple Quantnet[{n_neurons}, {look_back}]")
    return QuantNet(n_neurons, look_back)


def basic_training(model: QuantNet, train_dataset: Tensor, train_labels: Tensor):
    log.info("Training model...")
    loss_function = MSELoss()
    optimizer = Adam(model.parameters(), lr=0.001)

    loss_curve = []
    for _ in tqdm(range(300)):
        loss_total = 0

        model.zero_grad()

        predictions = model(train_dataset)

        loss = loss_function(predictions, train_labels)
        loss_total += loss.item()
        loss.backward()
        optimizer.step()
        loss_curve.append(loss_total)
    log.info(f"Done training, loss: {loss_total:.4%}")
