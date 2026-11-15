"""Load MERT-v2-30s and pool one 30-second clip into a 1,024-d vector."""

import os
import threading

import numpy as np
import torch
from transformers import AutoFeatureExtractor, AutoModel

MODEL_ID = os.environ.get("MERT_MODEL", "m-a-p/MERT-v2-30s")


class Encoder:
    def __init__(self) -> None:
        requested = os.environ.get("MERT_DEVICE", "cpu")
        if requested == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("MERT_DEVICE=cuda but this process cannot use CUDA.")
            self.device = "cuda"
        else:
            self.device = "cpu"
        self.model_id = MODEL_ID
        self.processor = None
        self.model = None
        self.sample_rate = 24000
        self.dimension = 1024
        self.layers = 24
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        self.processor = AutoFeatureExtractor.from_pretrained(
            self.model_id, trust_remote_code=True
        )
        self.model = AutoModel.from_pretrained(
            self.model_id, trust_remote_code=True
        ).eval().to(self.device)
        if self.device == "cuda":
            name = torch.cuda.get_device_name(0)
            capability = torch.cuda.get_device_capability(0)
            print(f"MERT loaded on {name} (sm_{capability[0]}{capability[1]})", flush=True)
        self.sample_rate = int(self.processor.sampling_rate)
        self.dimension = int(self.model.config.hidden_size)
        self.layers = int(self.model.config.num_hidden_layers)

    def embed(self, waveform: np.ndarray, layer: int | None) -> np.ndarray:
        if layer is not None and not 1 <= layer <= self.layers:
            raise ValueError(f"Layer must be from 1 to {self.layers}.")
        inputs = self.processor(
            waveform,
            sampling_rate=self.sample_rate,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._lock, torch.inference_mode():
            output = self.model(**inputs, output_hidden_states=layer is not None)
        if layer is None:
            frames = output.last_hidden_state
        else:
            frames = output.hidden_states[layer - 1]
        mask = getattr(output, "feature_attention_mask", None)
        if mask is None:
            pooled = frames.mean(dim=1)
        else:
            weights = mask.to(dtype=frames.dtype).unsqueeze(-1)
            pooled = (frames * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)
        vector = pooled.squeeze(0).float().cpu().numpy()
        if vector.shape != (self.dimension,):
            raise RuntimeError(
                f"Expected a {self.dimension}-d embedding, got shape {tuple(vector.shape)}."
            )
        return vector


encoder = Encoder()
