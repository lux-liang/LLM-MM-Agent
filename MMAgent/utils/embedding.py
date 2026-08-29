"""Semantic method retrieval used by the research pipeline."""

from __future__ import annotations

from typing import List

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer


class EmbeddingScorer:
    """Score candidate methods with multilingual transformer embeddings."""

    def __init__(self, model_name: str = "Alibaba-NLP/gte-multilingual-base"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name, trust_remote_code=True)
        self.model.eval()
        self.dimension = 768

    def score_method(self, query: str, methods: List[dict]) -> List[dict]:
        if not methods:
            return []
        sentences = [
            f"{method.get('method', '')}: {method.get('description', '')}"
            for method in methods
        ]
        batch = self.tokenizer(
            [query] + sentences,
            max_length=8192,
            padding=True,
            truncation=True,
            return_tensors="pt",
        )
        with torch.no_grad():
            outputs = self.model(**batch)
        # The previous [:dimension] sliced the batch axis and silently dropped
        # candidates once there were more than 768.  Slice the hidden axis.
        embeddings = outputs.last_hidden_state[:, 0, : self.dimension]
        embeddings = F.normalize(embeddings, p=2, dim=1)
        similarities = (embeddings[0:1] @ embeddings[1:].T).squeeze(0) * 100
        values = similarities.detach().cpu().numpy().reshape(-1).tolist()
        return [
            {"method_index": index, "score": float(score)}
            for index, score in enumerate(values, start=1)
        ]
