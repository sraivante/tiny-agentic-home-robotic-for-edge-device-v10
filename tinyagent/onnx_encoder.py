"""CPU ONNX word encoder with the same tensor interface as the training encoder."""
from types import SimpleNamespace

import torch
from torch import nn


class OnnxWordEncoder(nn.Module):
    def __init__(self, graph, threads):
        super().__init__()
        if isinstance(graph, torch.Tensor):
            if graph.dtype != torch.uint8 or graph.ndim != 1:
                raise ValueError("ONNX graph storage must be a flat uint8 tensor")
            graph = graph.cpu().numpy().tobytes()
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Avoid idle thread spinning on the laptop or future battery-powered Pi.
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = ort.InferenceSession(graph, sess_options=options, providers=["CPUExecutionProvider"])

    def forward(self, input_ids, attention_mask):
        output = self.session.run(["last_hidden_state"], {
            "input_ids": input_ids.detach().cpu().numpy(),
            "attention_mask": attention_mask.long().detach().cpu().numpy(),
        })[0]
        return SimpleNamespace(last_hidden_state=torch.from_numpy(output))
