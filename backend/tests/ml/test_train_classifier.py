#!/usr/bin/env python3
"""
Tests for train_classifier module.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.ml.train_classifier import (
    CustomTrainer,
    MetricsCallback,
    CLASS_LABELS,
    DEFAULT_CLASSES,
    tokenize_function,
    compute_class_weights,
)


class TestTrainClassifier:
    """Test the train_classifier module."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.class_names = DEFAULT_CLASSES

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_class_mapping(self):
        """Test class label mapping."""
        assert CLASS_LABELS == {
            "benign": 0,
            "prompt_injection": 1,
            "jailbreak": 2,
            "suspicious": 3
        }
        assert DEFAULT_CLASSES == ["benign", "prompt_injection", "jailbreak", "suspicious"]

    def test_tokenize_function(self):
        """Test tokenization function."""
        from transformers import AutoTokenizer
        
        # Mock tokenizer
        tokenizer = MagicMock()
        tokenizer.return_value = {
            "input_ids": [[1, 2, 3]],
            "attention_mask": [[1, 1, 1]]
        }
        
        examples = {"text": ["test prompt"]}
        result = tokenize_function(examples, tokenizer, max_length=512)
        
        tokenizer.assert_called_once()
        assert "input_ids" in result
        assert "attention_mask" in result

    def test_compute_class_weights(self):
        """Test class weight computation."""
        # Mock dataset
        dataset = {
            "label": [0, 0, 1, 1, 1, 2, 2, 3, 3, 3]  # Imbalanced: 2, 3, 2, 3 samples
        }
        
        weights = compute_class_weights(dataset)
        
        assert len(weights) == 4  # 4 classes
        assert all(w > 0 for w in weights)
        # Check that weights are inversely proportional to class frequencies
        assert weights[0] > weights[1]  # Class 0 has fewer samples than class 1

    def test_custom_trainer_init(self):
        """Test CustomTrainer initialization."""
        from transformers import TrainingArguments
        
        # Mock model and arguments
        model = MagicMock()
        args = TrainingArguments(output_dir=self.temp_dir)
        class_weights = torch.tensor([1.0, 1.0, 1.0, 1.0])
        
        trainer = CustomTrainer(
            model=model,
            args=args,
            train_dataset=None,
            eval_dataset=None,
            class_weights=class_weights
        )
        
        assert trainer.model == model
        assert trainer.args == args
        assert trainer.class_weights == class_weights

    def test_metrics_callback_init(self):
        """Test MetricsCallback initialization."""
        callback = MetricsCallback(self.temp_dir, self.class_names)
        
        assert callback.output_dir == self.temp_dir
        assert callback.class_names == self.class_names
        assert callback.best_metrics is None

    def test_metrics_callback_on_evaluate(self):
        """Test MetricsCallback on_evaluate method."""
        callback = MetricsCallback(self.temp_dir, self.class_names)
        
        # Mock evaluation metrics
        mock_metrics = MagicMock()
        mock_metrics.eval_f1_macro = 0.85
        mock_metrics.eval_accuracy = 0.90
        mock_metrics.eval_f1_per_class = {
            "benign": 0.88,
            "prompt_injection": 0.82,
            "jailbreak": 0.85,
            "suspicious": 0.87
        }
        
        # Mock args, state, control
        mock_args = MagicMock()
        mock_state = MagicMock()
        mock_control = MagicMock()
        
        # Call on_evaluate
        callback.on_evaluate(mock_args, mock_state, mock_control, metrics=mock_metrics)
        
        # Check that metrics were saved
        assert callback.best_metrics is not None
        assert callback.best_metrics["eval_f1_macro"] == 0.85
        assert callback.best_metrics["eval_accuracy"] == 0.90
        
        # Check that JSON file was created
        metrics_path = self.temp_dir / "metrics.json"
        assert metrics_path.exists()
        
        # Verify JSON content
        with open(metrics_path, 'r') as f:
            saved_metrics = json.load(f)
        
        assert saved_metrics == callback.best_metrics

    @patch('app.ml.train_classifier.load_dataset_splits')
    @patch('app.ml.train_classifier.AutoTokenizer')
    @patch('app.ml.train_classifier.AutoModelForSequenceClassification')
    @patch('app.ml.train_classifier.compute_class_weights')
    @patch('app.ml.train_classifier.CustomTrainer')
    def test_main_smoke_mode(self, mock_trainer, mock_weights, mock_model, mock_tokenizer, mock_load_data):
        """Test main function in smoke mode."""
        # Mock dataset loading
        mock_dataset = MagicMock()
        mock_dataset.map.return_value = mock_dataset
        mock_dataset.__len__.return_value = 10
        mock_load_data.return_value = (mock_dataset, mock_dataset, mock_dataset)
        
        # Mock tokenizer
        mock_tokenizer.from_pretrained.return_value = MagicMock()
        mock_tokenizer.return_value = MagicMock()
        
        # Mock model
        mock_model.from_pretrained.return_value = MagicMock()
        
        # Mock class weights
        mock_weights.return_value = torch.tensor([1.0, 1.0, 1.0, 1.0])
        
        # Mock trainer
        mock_trainer_instance = MagicMock()
        mock_trainer_instance.train.return_value = None
        mock_trainer_instance.predict.return_value = MagicMock(predictions=np.array([[0.1, 0.2, 0.3, 0.4]] * 10))
        mock_trainer_instance.evaluate.return_value = {
            "eval_f1_macro": 0.85,
            "eval_accuracy": 0.90,
            "eval_f1_per_class": {
                "benign": 0.88,
                "prompt_injection": 0.82,
                "jailbreak": 0.85,
                "suspicious": 0.87
            }
        }
        mock_trainer.return_value = mock_trainer_instance
        
        # Mock sys.exit to avoid actual exit
        with patch('sys.exit') as mock_exit:
            # Import and run main with smoke flag
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent))
            
            # Mock command line arguments
            with patch('sys.argv', ['train_classifier.py', '--smoke', '--data-dir', str(self.temp_dir)]):
                from app.ml.train_classifier import main
                main()
        
        # Check that trainer was called with smoke mode parameters
        mock_trainer.assert_called_once()
        mock_trainer_instance.train.assert_called_once()
        
        # Check that exit was not called (F1 threshold passed)
        mock_exit.assert_not_called()

    def test_load_dataset_splits_smoke(self):
        """Test loading dataset splits in smoke mode."""
        # Create mock dataset files
        for split in ["train", "val", "test"]:
            split_path = self.temp_dir / f"{split}.jsonl"
            with open(split_path, 'w') as f:
                for i in range(100):  # More than smoke samples
                    f.write(json.dumps({
                        "text": f"sample {i}",
                        "label": i % 4,
                        "source": "test"
                    }) + '\n')
        
        # Import and test function
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from app.ml.train_classifier import load_dataset_splits
        
        train, val, test = load_dataset_splits(self.temp_dir, smoke=True)
        
        # Check that datasets were sampled
        assert len(train) == 200  # DEFAULT_SMOKE_SAMPLES
        assert len(val) == 200
        assert len(test) == 200

    def test_load_dataset_splits_full(self):
        """Test loading dataset splits in full mode."""
        # Create mock dataset files
        for split in ["train", "val", "test"]:
            split_path = self.temp_dir / f"{split}.jsonl"
            with open(split_path, 'w') as f:
                for i in range(50):
                    f.write(json.dumps({
                        "text": f"sample {i}",
                        "label": i % 4,
                        "source": "test"
                    }) + '\n')
        
        # Import and test function
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from app.ml.train_classifier import load_dataset_splits
        
        train, val, test = load_dataset_splits(self.temp_dir, smoke=False)
        
        # Check that full datasets were loaded
        assert len(train) == 50
        assert len(val) == 50
        assert len(test) == 50