from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from app.ml.triton_client import TritonEmbeddingClient, get_shared_triton_embedding_client, set_shared_triton_embedding_client


class TestTritonEmbeddingClient:
    """Test the Triton embedding client wrapper."""

    def test_init_success(self):
        """Test successful initialization."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer",
                    max_length=512
                )
                
                assert client.triton_url == "localhost:8001"
                assert client.model_name == "multilingual_e5"
                assert client.available is True
                mock_client.assert_called_once_with(url="localhost:8001", verbose=False)
                mock_tokenizer.from_pretrained.assert_called_once_with("fake_tokenizer")

    def test_init_server_not_available(self):
        """Test initialization with unavailable Triton server."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = False
            
            client = TritonEmbeddingClient(
                triton_url="localhost:8001",
                model_name="multilingual_e5",
                tokenizer_path="fake_tokenizer"
            )
            
            assert client.available is False

    def test_init_model_not_ready(self):
        """Test initialization with model not ready."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = False
            
            client = TritonEmbeddingClient(
                triton_url="localhost:8001",
                model_name="multilingual_e5",
                tokenizer_path="fake_tokenizer"
            )
            
            assert client.available is False

    def test_tokenize_sync(self):
        """Test text tokenization."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                mock_tokenizer.return_value = {
                    'input_ids': np.array([[1, 2, 3]]),
                    'attention_mask': np.array([[1, 1, 1]])
                }
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer"
                )
                
                tokenized = client._tokenize_sync("test text")
                assert 'input_ids' in tokenized
                assert 'attention_mask' in tokenized
                assert tokenized['input_ids'].dtype == np.int64
                assert tokenized['attention_mask'].dtype == np.int64

    def test_pool_and_norm(self):
        """Test mean pooling + L2 normalization."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer"
                )
                
                # Test with mask
                hidden = np.array([[[1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]])  # batch=1, seq=3, dim=2
                mask = np.array([[1, 1, 0]])
                result = client._pool_and_norm(hidden, mask)
                
                # Mean pooling: (1+3)/2 = 2, (2+4)/2 = 3 → [2, 3]
                # L2 norm: sqrt(2² + 3²) = sqrt(13) ≈ 3.606
                # Normalized: [2/3.606, 3/3.606] ≈ [0.555, 0.832]
                expected = np.array([0.555, 0.832]).astype(np.float32)
                np.testing.assert_allclose(result, expected, rtol=1e-3)
                
                # Test without mask
                result_no_mask = client._pool_and_norm(hidden)
                # Mean pooling: (1+2+3+4)/4 = 2.5, (0+0+0+0)/4 = 0 → [2.5, 0]
                # L2 norm: sqrt(2.5² + 0²) = 2.5
                # Normalized: [1.0, 0.0]
                expected_no_mask = np.array([1.0, 0.0]).astype(np.float32)
                np.testing.assert_allclose(result_no_mask, expected_no_mask, rtol=1e-3)

    @pytest.mark.asyncio
    async def test_embed_success(self):
        """Test successful embedding inference."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                mock_tokenizer.return_value = {
                    'input_ids': np.array([[1, 2, 3]]),
                    'attention_mask': np.array([[1, 1, 1]])
                }
                
                # Mock Triton inference result
                mock_results = MagicMock()
                mock_results.as_numpy.return_value = np.array([[0.1, 0.2, 0.3, 0.4]])  # 4-dim for testing
                mock_client.return_value.infer.return_value = mock_results
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer",
                    embedding_dim=4
                )
                
                result = await client.embed("test text")
                
                assert result is not None
                assert len(result) == 4
                assert result.dtype == np.float32
                mock_client.return_value.infer.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_unavailable(self):
        """Test embedding with unavailable Triton."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = False
            
            client = TritonEmbeddingClient(
                triton_url="localhost:8001",
                model_name="multilingual_e5",
                tokenizer_path="fake_tokenizer"
            )
            
            result = await client.embed("test text")
            assert result is None

    @pytest.mark.asyncio
    async def test_embed_empty_text(self):
        """Test embedding with empty text."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer"
                )
                
                result = await client.embed("")
                assert result is None

    @pytest.mark.asyncio
    async def test_embed_batch_success(self):
        """Test successful batch embedding inference."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                
                # Mock tokenizer for different sequence lengths
                def mock_tokenize(text):
                    if text == "short":
                        return {'input_ids': np.array([[1, 2]]), 'attention_mask': np.array([[1, 1]])}
                    else:
                        return {'input_ids': np.array([[1, 2, 3]]), 'attention_mask': np.array([[1, 1, 1]])}
                
                mock_tokenizer.return_value.side_effect = mock_tokenize
                
                # Mock Triton inference result
                mock_results = MagicMock()
                mock_results.as_numpy.return_value = np.array([
                    [[0.1, 0.2]],    # short text
                    [[0.3, 0.4, 0.5]]  # longer text
                ])
                mock_client.return_value.infer.return_value = mock_results
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer",
                    embedding_dim=2
                )
                
                texts = ["short", "longer"]
                results = await client.embed_batch(texts)
                
                assert len(results) == 2
                assert all(r is not None for r in results)
                assert all(len(r) == 2 for r in results)
                mock_client.return_value.infer.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_batch_unavailable(self):
        """Test batch embedding with unavailable Triton."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = False
            
            client = TritonEmbeddingClient(
                triton_url="localhost:8001",
                model_name="multilingual_e5",
                tokenizer_path="fake_tokenizer"
            )
            
            results = await client.embed_batch(["text1", "text2"])
            assert len(results) == 2
            assert all(r is None for r in results)

    def test_singleton_pattern(self):
        """Test singleton get/set pattern."""
        # Test get (initially None)
        assert get_shared_triton_embedding_client() is None
        
        # Test set and get
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer"
                )
                
                set_shared_triton_embedding_client(client)
                assert get_shared_triton_embedding_client() is client
                
                # Test set None
                set_shared_triton_embedding_client(None)
                assert get_shared_triton_embedding_client() is None


class TestTritonEmbeddingIntegration:
    """Integration tests with mocked dependencies."""

    def test_client_interface_compatibility(self):
        """Test that Triton client has same interface as EmbeddingClient."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer"
                )
                
                # Check required attributes/methods exist
                assert hasattr(client, 'embed')
                assert hasattr(client, 'embed_batch')
                assert hasattr(client, 'available')
                assert callable(client.embed)
                assert callable(client.embed_batch)
                assert isinstance(client.available, bool)

    @pytest.mark.asyncio
    async def test_embed_batch_success(self):
        """Test successful batch embedding."""
        texts = ["test text 1", "test text 2", "test text 3"]
        expected_embeddings = [np.random.rand(1024).astype(np.float32) for _ in texts]
        
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = True
            mock_client.return_value.is_model_ready.return_value = True
            
            with patch('app.ml.triton_client.AutoTokenizer') as mock_tokenizer:
                mock_tokenizer.from_pretrained.return_value = MagicMock()
                mock_tokenizer.return_value = {
                    'input_ids': np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]]),
                    'attention_mask': np.array([[1, 1, 1], [1, 1, 1], [1, 1, 1]])
                }
                
                client = TritonEmbeddingClient(
                    triton_url="localhost:8001",
                    model_name="multilingual_e5",
                    tokenizer_path="fake_tokenizer"
                )
                
                # Mock the infer call to return expected embeddings
                mock_results = MagicMock()
                mock_results.as_numpy.return_value = np.array([emb.reshape(1, 3, 1024) for emb in expected_embeddings])
                mock_client.return_value.infer.return_value = mock_results
                
                embeddings = await client.embed_batch(texts)
                
                # Verify all embeddings were processed
                assert len(embeddings) == len(texts)
                assert all(embedding is not None for embedding in embeddings)
                
                # Verify infer was called once with batch size 3
                mock_client.return_value.infer.assert_called_once()
                call_args = mock_client.return_value.infer.call_args
                assert call_args[1]['inputs'][0].shape[0] == 3  # batch size
                
                # Verify embeddings are returned in correct order
                for i, embedding in enumerate(embeddings):
                    assert embedding.shape == (1024,)
                    # Note: exact values would require more complex mocking of _pool_and_norm

    @pytest.mark.asyncio
    async def test_embed_batch_unavailable(self):
        """Test batch embedding when Triton is unavailable."""
        with patch('app.ml.triton_client.grpcclient.InferenceServerClient') as mock_client:
            mock_client.return_value.is_server_live.return_value = False
            
            client = TritonEmbeddingClient(
                triton_url="localhost:8001",
                model_name="multilingual_e5",
                tokenizer_path="fake_tokenizer"
            )
            
            embeddings = await client.embed_batch(["test text 1", "test text 2"])
            
            # Should return None for all texts when unavailable
            assert len(embeddings) == 2
            assert all(embedding is None for embedding in embeddings)