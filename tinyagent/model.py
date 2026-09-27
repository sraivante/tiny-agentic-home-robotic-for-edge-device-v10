from __future__ import annotations

import math
import os

import torch
from torch import nn
from torch.nn import functional as F


class TinyCommandModel(nn.Module):
    """Character encoder + action head + typed value/copy heads; no external LLM."""

    def __init__(self, metadata, width=128, layers=3, dropout=.1, semantic_config=None, semantic_action=False, intent_fusion=False,
                 semantic_action_full=False, intent_width=None):
        super().__init__()
        self.config = {"width": width, "layers": layers, "dropout": dropout}
        self.semantic_action = semantic_action
        self.intent_fusion = intent_fusion
        self.semantic_action_full = semantic_action_full
        if semantic_action_full:
            if not semantic_action or semantic_config is None:
                raise ValueError("Full semantic intent requires a semantic action encoder")
            self.config["semantic_action_full"] = True
        if intent_width is not None:
            if intent_width < width:
                raise ValueError("Intent width cannot be smaller than the character width")
            self.config["intent_width"] = intent_width
        intent_hidden = intent_width if intent_width is not None else width
        if intent_fusion:
            if not semantic_action:
                raise ValueError("Character intent fusion requires semantic action classification")
            self.config["intent_fusion"] = True
        if semantic_action:
            if semantic_config is None:
                raise ValueError("Semantic action classification requires a word encoder")
            self.config["semantic_action"] = True
        self.word_encoder = None
        if semantic_config is not None:
            os.environ.setdefault("USE_TF", "0")
            os.environ.setdefault("USE_FLAX", "0")
            from transformers import BertConfig, BertModel
            self.config["semantic_config"] = semantic_config
            self.word_encoder = BertModel(BertConfig(**semantic_config), add_pooling_layer=False)
            self.word_projection = nn.Linear(semantic_config["hidden_size"], width)
            self.word_scale = nn.Parameter(torch.tensor(.1))
        self.meta = metadata
        self.embedding = nn.Embedding(len(metadata["chars"]), width, padding_idx=0)
        self.position = nn.Embedding(metadata["max_length"], width)
        self.local = nn.Conv1d(width, width, 5, padding=2, groups=4)
        block = nn.TransformerEncoderLayer(width, 4, width * 3, dropout, activation="gelu", batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(block, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)
        intent_input = semantic_config["hidden_size"] * 2 if semantic_action_full else width * 2
        self.intent = nn.Sequential(nn.Linear(intent_input, intent_hidden), nn.GELU(), nn.Dropout(dropout),
                                    nn.Linear(intent_hidden, len(metadata["actions"])))
        if intent_fusion:
            self.character_intent = nn.Sequential(nn.LayerNorm(width * 2), nn.Linear(width * 2, width),
                                                  nn.GELU(), nn.Dropout(dropout), nn.Linear(width, len(metadata["actions"])))
            # A new residual branch starts with exactly the parent's predictions.
            nn.init.zeros_(self.character_intent[-1].weight)
            nn.init.zeros_(self.character_intent[-1].bias)
        self.action_embedding = nn.Embedding(len(metadata["actions"]), width)
        self.slot_embedding = nn.Embedding(len(metadata["slots"]), width)
        self.query = nn.Linear(width * 3, width)
        self.context_key = nn.Linear(width, width)
        self.start_key = nn.Linear(width, width)
        self.end_key = nn.Linear(width, width)
        self.start_query = nn.Linear(width * 2, width)
        self.end_query = nn.Linear(width * 2, width)
        self.value = nn.Linear(width * 2, max(map(len, metadata["labels"])))
        valid = torch.zeros(len(metadata["slots"]), self.value.out_features, dtype=torch.bool)
        for k, labels in enumerate(metadata["labels"]):
            valid[k, :len(labels)] = True
        self.register_buffer("valid_labels", valid)
        allowed = torch.zeros(len(metadata["actions"]), len(metadata["slots"]), dtype=torch.bool)
        for a, slots in enumerate(metadata["allowed"]):
            allowed[a, slots] = True
        self.register_buffer("allowed", allowed)

    def encode(self, x, pieces=None, alignment=None):
        padding = x == 0
        h = self.embedding(x) + self.position(torch.arange(x.shape[1], device=x.device)) * .15
        if self.word_encoder is not None:
            if pieces is None or alignment is None:
                raise ValueError("The hybrid encoder requires token IDs and character alignment")
            word_features = self.word_encoder(input_ids=pieces, attention_mask=pieces.ne(0)).last_hidden_state
            semantic = self.word_projection(word_features)
            h = h + self.word_scale * semantic.gather(1, alignment[..., None].expand(-1, -1, semantic.shape[-1]))
        if self.semantic_action:
            # Keep the local convolution independent of neighboring batch padding.
            h = h.masked_fill(padding[..., None], 0)
        h = h + F.gelu(self.local(h.transpose(1, 2)).transpose(1, 2))
        h = self.norm(self.encoder(h, src_key_padding_mask=padding))
        mean = h.masked_fill(padding[..., None], 0).sum(1) / (~padding).sum(1, keepdim=True).clamp_min(1)
        maximum = h.masked_fill(padding[..., None], -1e4).max(1).values
        character_features = torch.cat((mean, maximum), -1)
        intent_features = character_features
        if self.semantic_action:
            mask = pieces.ne(0)[..., None]
            action_features = word_features if self.semantic_action_full else semantic
            word_mean = (action_features * mask).sum(1) / mask.sum(1).clamp_min(1)
            intent_features = torch.cat((action_features[:, 0], word_mean), -1)
        logits = self.intent(intent_features)
        if self.intent_fusion:
            logits = logits + self.character_intent(character_features)
        return h, mean, logits, padding

    def decode(self, h, pooled, padding, actions):
        batch, slots = self.allowed[actions].nonzero(as_tuple=True)
        if not batch.numel():
            return batch, slots, None, None, None
        q = torch.tanh(self.query(torch.cat((self.slot_embedding(slots), self.action_embedding(actions[batch]), pooled[batch]), -1)))
        keys = self.context_key(h)[batch]
        mask = padding[batch]
        attention = torch.bmm(keys, q.unsqueeze(-1)).squeeze(-1) / math.sqrt(h.shape[-1])
        attention = attention.masked_fill(mask, -1e4).softmax(-1)
        context = torch.bmm(attention.unsqueeze(1), h[batch]).squeeze(1)
        joint = torch.cat((q, context), -1)
        mode = self.value(joint).masked_fill(~self.valid_labels[slots], -1e4)
        start = torch.bmm(self.start_key(h)[batch], self.start_query(joint).unsqueeze(-1)).squeeze(-1)
        end = torch.bmm(self.end_key(h)[batch], self.end_query(joint).unsqueeze(-1)).squeeze(-1)
        return batch, slots, mode, start.masked_fill(mask, -1e4), end.masked_fill(mask, -1e4)

    def forward(self, x, actions=None, pieces=None, alignment=None):
        h, pooled, logits, padding = self.encode(x, pieces, alignment)
        return logits, self.decode(h, pooled, padding, logits.argmax(-1) if actions is None else actions)


def lift_semantic_intent_state(weights, parent_config, model):
    """Preserve an existing intent function while exposing full word features.

    Compose the parent's shared word projection into the first intent layer.
    Extra hidden units retain their fresh inputs and start with zero output
    weights, so they can learn while the initial predictions remain unchanged.
    This performs no optimizer steps and does not inspect labels or requests.
    """
    if not model.semantic_action_full or not parent_config.get("semantic_action"):
        raise ValueError("Lifting requires an existing semantic classifier and full-feature target")
    first, bias = weights["intent.0.weight"], weights["intent.0.bias"]
    if not parent_config.get("semantic_action_full"):
        projection, offset = weights["word_projection.weight"], weights["word_projection.bias"]
        left, right = first.split(projection.shape[0], dim=1)
        first = torch.cat((left @ projection, right @ projection), dim=1)
        bias = bias + (left + right) @ offset
    old_hidden = first.shape[0]
    target = model.state_dict()
    if target["intent.0.weight"].shape[0] < old_hidden or target["intent.0.weight"].shape[1] != first.shape[1]:
        raise ValueError("Target intent head cannot preserve the parent dimensions")
    lifted = dict(weights)
    lifted["intent.0.weight"] = target["intent.0.weight"].clone()
    lifted["intent.0.bias"] = target["intent.0.bias"].clone()
    lifted["intent.0.weight"][:old_hidden] = first
    lifted["intent.0.bias"][:old_hidden] = bias
    lifted["intent.3.weight"] = torch.zeros_like(target["intent.3.weight"])
    lifted["intent.3.weight"][:, :old_hidden] = weights["intent.3.weight"]
    return lifted
