"""Optional CPU quantization of the pretrained word encoder only.

The character encoder and typed copy/value heads retain float32 weights.
Packed weights are reconstructed on the host's supported quantized backend.
"""
import platform

import torch
from torch import nn


FORMAT = "semantic_word_int8_v1"


def quantize_word_encoder(model):
    if model.word_encoder is None:
        raise ValueError("INT8 conversion requires a word encoder")
    available = set(torch.backends.quantized.supported_engines) - {"none"}
    machine = platform.machine().lower()
    choices = ["qnnpack"] if machine in {"aarch64", "arm64", "armv7l"} else ["x86", "onednn", "fbgemm"]
    engine = next((name for name in choices if name in available), None)
    if engine is None:
        raise RuntimeError(f"No supported INT8 backend for {machine}; available: {sorted(available)}")
    torch.backends.quantized.engine = engine
    from torch.ao.quantization import (
        float_qparams_weight_only_qconfig,
        per_channel_dynamic_qconfig,
        quantize_dynamic,
    )
    model.word_encoder = quantize_dynamic(
        model.word_encoder,
        {nn.Linear: per_channel_dynamic_qconfig, "embeddings.word_embeddings": float_qparams_weight_only_qconfig},
        inplace=True,
    )
    return engine
