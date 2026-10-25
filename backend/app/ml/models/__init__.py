"""ML model wrappers for the LLM Security Scanner."""

from .classifier_model import ClassifierModel, load_classifier_model, get_classifier_model, set_classifier_model
from .toxicity_model import ToxicityModel, load_toxicity_model, get_toxicity_model, set_toxicity_model

__all__ = [
    "ClassifierModel",
    "load_classifier_model",
    "get_classifier_model",
    "set_classifier_model",
    "ToxicityModel",
    "load_toxicity_model",
    "get_toxicity_model",
    "set_toxicity_model",
]