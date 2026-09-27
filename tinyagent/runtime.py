from __future__ import annotations

import json
import hashlib
import io
import time
from pathlib import Path

import torch

from .model import TinyCommandModel
from .schema import lower_chars, target_string, validate_command


def typed_copy(value, expected):
    """Restore JSON integers while retaining hex literals and named speeds.

    Training uses int/str unions for I2C values and mouse speeds: decimal
    digits are integers, whereas 0xff and fast/slow remain strings.
    """
    if "int" in expected:
        try:
            return int(value)
        except ValueError:
            pass
    return value


class Predictor:
    def __init__(self, checkpoint, threads=4, device="cpu"):
        torch.set_num_threads(threads)
        self.device = torch.device(device)
        if self.device.type == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA evaluation requested but no CUDA device is available")
        checkpoint_bytes = Path(checkpoint).read_bytes()
        self.checkpoint_sha256 = hashlib.sha256(checkpoint_bytes).hexdigest()
        self.model_bytes = len(checkpoint_bytes)
        payload = torch.load(io.BytesIO(checkpoint_bytes), map_location="cpu", weights_only=True)
        self.meta = payload["metadata"]
        self.decoding = payload.get("decoding", {"version": 1})
        self.model = TinyCommandModel(self.meta, **payload["config"])
        self.parameters = sum(p.numel() for p in self.model.parameters())
        self.quantization = payload.get("quantization")
        self.derivation = payload.get("derived_from")
        self.execution = payload.get("execution", {"backend": "pytorch"})
        if "word_encoder_onnx" in payload:
            if self.device.type != "cpu" or self.quantization:
                raise ValueError("The ONNX word encoder requires CPU and its own checkpoint format")
            if self.execution.get("backend") != "onnx-word-v1":
                raise ValueError("Unrecognized ONNX checkpoint format")
            from .onnx_encoder import OnnxWordEncoder
            self.model.word_encoder = OnnxWordEncoder(payload["word_encoder_onnx"], threads)
        if self.quantization:
            from .quantization import FORMAT, quantize_word_encoder
            if self.quantization.get("format") != FORMAT:
                raise ValueError("Unrecognized quantized checkpoint format")
            if self.device.type != "cpu":
                raise ValueError("This INT8 checkpoint requires CPU inference")
            self.quantization = dict(self.quantization, runtime_backend=quantize_word_encoder(self.model))
        self.model.load_state_dict(payload["state_dict"])
        self.model.to(self.device)
        self.model.eval()
        self.characters = {c: i for i, c in enumerate(self.meta["chars"])}
        self.checkpoint = str(Path(checkpoint).resolve())
        self.epoch = payload["epoch"]
        self.tokenizer = None
        if "tokenizer_json" in self.meta:
            from tokenizers import Tokenizer
            self.tokenizer = Tokenizer.from_str(self.meta["tokenizer_json"])

    @torch.inference_mode()
    def predict(self, texts):
        if isinstance(texts, str):
            return self.predict([texts])[0]
        if not texts:
            return []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Enter a nonempty command")
            if len(text) > self.meta["max_length"]:
                raise ValueError(f"Commands must be at most {self.meta['max_length']} characters")
        begin = time.perf_counter()
        x = torch.zeros(len(texts), max(map(len, texts)), dtype=torch.long)
        for i, text in enumerate(texts):
            x[i, :len(text)] = torch.tensor([self.characters.get(c, 1) for c in lower_chars(text)])
        word_inputs = {}
        if self.tokenizer is not None:
            from .tokenization import encode_words
            pieces, alignment = encode_words(self.tokenizer, texts, x.shape[1])
            word_inputs = {"pieces": torch.from_numpy(pieces).long(), "alignment": torch.from_numpy(alignment).long()}
        logits, decoded = self.model(x.to(self.device), **{k:v.to(self.device) for k,v in word_inputs.items()})
        if self.device.type != "cpu":
            # Decode typed strings on CPU in one transfer, avoiding a GPU sync per slot.
            logits = logits.cpu()
            decoded = tuple(value.cpu() if value is not None else None for value in decoded)
        probabilities = logits.softmax(-1)
        confidence, action_ids = probabilities.max(-1)
        top = probabilities.topk(3, dim=-1)
        results = [{"action": self.meta["actions"][a], "args": {}, "confidence": float(confidence[i]),
                    "alternatives": [{"action": self.meta["actions"][int(k)], "score": float(v)} for v, k in zip(top.values[i], top.indices[i])]}
                   for i, a in enumerate(action_ids.tolist())]
        batch, slots, modes, starts, ends = decoded
        if modes is not None:
            selected = modes.argmax(-1)
            for j, (i, s, m) in enumerate(zip(batch.tolist(), slots.tolist(), selected.tolist())):
                if m == 0:
                    continue
                key = self.meta["slots"][s]
                if m == 1:
                    # Jointly maximize a valid, ordered source span.
                    start_score, best_start = starts[j].cummax(0)
                    stop = int((start_score + ends[j]).argmax())
                    begin_at = int(best_start[stop])
                    expected = self.meta["types"].get(results[i]["action"], {}).get(key, [])
                    if self.decoding.get("version") in {2, 3}:
                        from .decoding import constrained_span
                        begin_at, stop = constrained_span(texts[i], key, expected, begin_at, stop, starts[j], ends[j],
                                                         numeric=self.decoding["version"] == 2)
                    value = texts[i][begin_at:stop + 1]
                    value = typed_copy(value, expected)
                else:
                    value = json.loads(self.meta["labels"][s][m])
                results[i]["args"][key] = value
        elapsed = (time.perf_counter() - begin) * 1000 / len(texts)
        for r in results:
            r["target"] = target_string(r["action"], r["args"])
            r["validation_errors"] = validate_command(r["action"], r["args"])
            r["latency_ms"] = round(elapsed, 3)
            r["model_epoch"] = self.epoch
        return results
