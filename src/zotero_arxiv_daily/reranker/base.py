from abc import ABC, abstractmethod
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Type


def weighted_similarity_scores(similarity: np.ndarray) -> np.ndarray:
    """Apply the legacy recency weighting to a candidate-by-interest matrix."""
    matrix = np.asarray(similarity, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("similarity must be a two-dimensional matrix")
    if matrix.shape[1] == 0:
        raise ValueError("the interest corpus must not be empty")
    if not np.isfinite(matrix).all():
        raise ValueError("similarity must contain only finite values")
    weights = 1 / (1 + np.log10(np.arange(matrix.shape[1]) + 1))
    weights = weights / weights.sum()
    return (matrix * weights).sum(axis=1) * 10


class BaseReranker(ABC):
    def __init__(self, config:DictConfig):
        self.config = config

    def rerank(self, candidates:list[Paper], corpus:list[CorpusPaper]) -> list[Paper]:
        corpus = sorted(corpus,key=lambda x: x.added_date,reverse=True)
        sim = self.get_similarity_score([c.abstract for c in candidates], [c.abstract for c in corpus])
        assert sim.shape == (len(candidates), len(corpus))
        scores = weighted_similarity_scores(sim)
        for s,c in zip(scores,candidates):
            c.score = s
        candidates = sorted(candidates,key=lambda x: x.score,reverse=True)
        return candidates
    
    @abstractmethod
    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        raise NotImplementedError

registered_rerankers = {}

def register_reranker(name:str):
    def decorator(cls):
        registered_rerankers[name] = cls
        return cls
    return decorator

def get_reranker_cls(name:str) -> Type[BaseReranker]:
    if name not in registered_rerankers:
        raise ValueError(f"Reranker {name} not found")
    return registered_rerankers[name]
