"""Unit tests for qdrant_setup module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.db.qdrant_setup import (
    build_quantization_config,
    build_hnsw_config,
    create_collection,
    enable_scalar_quantization,
    verify_collection_config,
    ensure_collection,
)


class TestQuantizationConfig:
    """Test quantization configuration building."""

    def test_build_quantization_config_default(self) -> None:
        """Test building default quantization config."""
        config = build_quantization_config()
        
        assert config.scalar.type == models.ScalarType.INT8
        assert config.scalar.quantile == 0.99
        assert config.scalar.always_ram is True

    def test_build_quantization_config_custom(self) -> None:
        """Test building custom quantization config."""
        config = build_quantization_config(quantile=0.95, always_ram=False)
        
        assert config.scalar.type == models.ScalarType.INT8
        assert config.scalar.quantile == 0.95
        assert config.scalar.always_ram is False


class TestHNSWConfig:
    """Test HNSW configuration building."""

    def test_build_hnsw_config_default(self) -> None:
        """Test building default HNSW config."""
        config = build_hnsw_config()
        
        assert config.m == 16
        assert config.ef_construct == 128

    def test_build_hnsw_config_custom(self) -> None:
        """Test building custom HNSW config."""
        config = build_hnsw_config(m=32, ef_construct=256)
        
        assert config.m == 32
        assert config.ef_construct == 256


class TestCreateCollection:
    """Test collection creation function."""

    @pytest.mark.asyncio
    async def test_create_collection_with_quantization(self) -> None:
        """Test creating collection with quantization."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        await create_collection(
            mock_client,
            "test_collection",
            size=128,
            quantized=True,
        )
        
        # Verify create_collection was called
        mock_client.create_collection.assert_called_once()
        args, kwargs = mock_client.create_collection.call_args
        
        assert kwargs["collection_name"] == "test_collection"
        assert kwargs["vectors_config"].size == 128
        assert kwargs["vectors_config"].distance == "Cosine"
        assert kwargs["hnsw_config"].m == 16
        assert kwargs["hnsw_config"].ef_construct == 128
        assert kwargs["quantization_config"] is not None
        assert kwargs["quantization_config"].scalar.type == models.ScalarType.INT8

    @pytest.mark.asyncio
    async def test_create_collection_without_quantization(self) -> None:
        """Test creating collection without quantization."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        await create_collection(
            mock_client,
            "test_collection",
            size=128,
            quantized=False,
        )
        
        # Verify create_collection was called
        mock_client.create_collection.assert_called_once()
        args, kwargs = mock_client.create_collection.call_args
        
        assert kwargs["collection_name"] == "test_collection"
        assert kwargs["vectors_config"].size == 128
        assert kwargs["vectors_config"].distance == "Cosine"
        assert kwargs["hnsw_config"].m == 16
        assert kwargs["hnsw_config"].ef_construct == 128
        assert kwargs["quantization_config"] is None

    @pytest.mark.asyncio
    async def test_create_collection_custom_params(self) -> None:
        """Test creating collection with custom parameters."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        await create_collection(
            mock_client,
            "test_collection",
            size=256,
            quantized=True,
            distance="Euclidean",
        )
        
        # Verify create_collection was called
        mock_client.create_collection.assert_called_once()
        args, kwargs = mock_client.create_collection.call_args
        
        assert kwargs["vectors_config"].size == 256
        assert kwargs["vectors_config"].distance == "Euclidean"

    @pytest.mark.asyncio
    async def test_create_collection_uses_settings_embedding_dim(self) -> None:
        """Test creating collection uses settings.embedding_dim when size not specified."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        with patch('app.db.qdrant_setup.settings') as mock_settings:
            mock_settings.embedding_dim = 512
            
            await create_collection(
                mock_client,
                "test_collection",
                quantized=True,
            )
            
            # Verify create_collection was called with correct size
            mock_client.create_collection.assert_called_once()
            args, kwargs = mock_client.create_collection.call_args
            assert kwargs["vectors_config"].size == 512


class TestEnableQuantization:
    """Test quantization enabling function."""

    @pytest.mark.asyncio
    async def test_enable_scalar_quantization(self) -> None:
        """Test enabling scalar quantization on existing collection."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        await enable_scalar_quantization(
            mock_client,
            "existing_collection",
            quantile=0.95,
            always_ram=False,
        )
        
        # Verify update_collection was called
        mock_client.update_collection.assert_called_once()
        args, kwargs = mock_client.update_collection.call_args
        
        assert kwargs["collection_name"] == "existing_collection"
        assert kwargs["quantization_config"] is not None
        assert kwargs["quantization_config"].scalar.type == models.ScalarType.INT8
        assert kwargs["quantization_config"].scalar.quantile == 0.95
        assert kwargs["quantization_config"].scalar.always_ram is False


class TestVerifyCollectionConfig:
    """Test collection configuration verification."""

    @pytest.mark.asyncio
    async def test_verify_collection_config_correct(self) -> None:
        """Test verifying correct collection configuration."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection with correct configuration
        mock_collection_info = MagicMock()
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = MagicMock()
        mock_collection_info.config.quantization_config.scalar = MagicMock()
        mock_collection_info.config.quantization_config.scalar.type = "int8"
        mock_collection_info.config.quantization_config.scalar.quantile = 0.99
        mock_collection_info.config.quantization_config.scalar.always_ram = True
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        
        config = await verify_collection_config(
            mock_client,
            "test_collection",
            size=128,
            quantized=True,
        )
        
        # Verify get_collection was called
        mock_client.get_collection.assert_called_once_with("test_collection")
        
        # Verify returned config
        assert config == mock_collection_info.config

    @pytest.mark.asyncio
    async def test_verify_collection_config_size_mismatch(self) -> None:
        """Test verification fails on size mismatch."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection with wrong size
        mock_collection_info = MagicMock()
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 64  # Wrong size
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = MagicMock()
        mock_collection_info.config.quantization_config.scalar = MagicMock()
        mock_collection_info.config.quantization_config.scalar.type = "int8"
        mock_collection_info.config.quantization_config.scalar.quantile = 0.99
        mock_collection_info.config.quantization_config.scalar.always_ram = True
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        
        with pytest.raises(AssertionError, match="Vector size mismatch"):
            await verify_collection_config(
                mock_client,
                "test_collection",
                size=128,  # Expected size
                quantized=True,
            )

    @pytest.mark.asyncio
    async def test_verify_collection_config_no_quantization_expected(self) -> None:
        """Test verification passes when no quantization expected."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection without quantization
        mock_collection_info = MagicMock()
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = None
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        
        config = await verify_collection_config(
            mock_client,
            "test_collection",
            size=128,
            quantized=False,
        )
        
        # Verify no assertion error
        assert config is not None

    @pytest.mark.asyncio
    async def test_verify_collection_config_quantization_missing(self) -> None:
        """Test verification fails when quantization expected but missing."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection without quantization but quantization expected
        mock_collection_info = MagicMock()
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = None
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        
        with pytest.raises(AssertionError, match="Quantization config missing"):
            await verify_collection_config(
                mock_client,
                "test_collection",
                size=128,
                quantized=True,
            )

    @pytest.mark.asyncio
    async def test_verify_collection_config_quantization_type_mismatch(self) -> None:
        """Test verification fails on quantization type mismatch."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection with wrong quantization type
        mock_collection_info = MagicMock()
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = MagicMock()
        mock_collection_info.config.quantization_config.scalar = MagicMock()
        mock_collection_info.config.quantization_config.scalar.type = "float32"  # Wrong type
        mock_collection_info.config.quantization_config.scalar.quantile = 0.99
        mock_collection_info.config.quantization_config.scalar.always_ram = True
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        
        with pytest.raises(AssertionError, match="Quantization type mismatch"):
            await verify_collection_config(
                mock_client,
                "test_collection",
                size=128,
                quantized=True,
            )


class TestEnsureCollection:
    """Test collection ensuring function."""

    @pytest.mark.asyncio
    async def test_ensure_collection_new_collection(self) -> None:
        """Test ensuring new collection."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection not found
        mock_client.get_collection.side_effect = UnexpectedResponse("Collection not found", 404, "Not Found", {})
        
        # Mock create_collection and verify_collection_config
        mock_client.create_collection = AsyncMock()
        mock_client.verify_collection_config = AsyncMock()
        
        await ensure_collection(
            mock_client,
            "new_collection",
            size=128,
            quantized=True,
        )
        
        # Verify calls
        mock_client.get_collection.assert_called_once_with("new_collection")
        mock_client.create_collection.assert_called_once()
        mock_client.verify_collection_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_collection_existing_correct(self) -> None:
        """Test ensuring existing collection with correct config."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection with correct configuration
        mock_collection_info = MagicMock()
        mock_collection_info.points_count = 100
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = MagicMock()
        mock_collection_info.config.quantization_config.scalar = MagicMock()
        mock_collection_info.config.quantization_config.scalar.type = "int8"
        mock_collection_info.config.quantization_config.scalar.quantile = 0.99
        mock_collection_info.config.quantization_config.scalar.always_ram = True
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        mock_client.verify_collection_config = AsyncMock()
        
        await ensure_collection(
            mock_client,
            "existing_collection",
            size=128,
            quantized=True,
        )
        
        # Verify calls
        mock_client.get_collection.assert_called_once_with("existing_collection")
        mock_client.verify_collection_config.assert_called_once()
        # create_collection should not be called
        mock_client.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_collection_mismatch_recreate(self) -> None:
        """Test ensuring collection with mismatch recreates it."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection with wrong configuration
        mock_collection_info = MagicMock()
        mock_collection_info.points_count = 100
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 64  # Wrong size
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = MagicMock()
        mock_collection_info.config.quantization_config.scalar = MagicMock()
        mock_collection_info.config.quantization_config.scalar.type = "int8"
        mock_collection_info.config.quantization_config.scalar.quantile = 0.99
        mock_collection_info.config.quantization_config.scalar.always_ram = True
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        mock_client.delete_collection = AsyncMock()
        mock_client.create_collection = AsyncMock()
        mock_client.verify_collection_config = AsyncMock()
        
        await ensure_collection(
            mock_client,
            "mismatch_collection",
            size=128,  # Expected size
            quantized=True,
        )
        
        # Verify calls
        mock_client.get_collection.assert_called_once_with("mismatch_collection")
        mock_client.delete_collection.assert_called_once_with("mismatch_collection")
        mock_client.create_collection.assert_called_once()
        mock_client.verify_collection_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_collection_force_recreate(self) -> None:
        """Test ensuring collection with force_recreate=True."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection with correct configuration
        mock_collection_info = MagicMock()
        mock_collection_info.points_count = 100
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = MagicMock()
        mock_collection_info.config.quantization_config.scalar = MagicMock()
        mock_collection_info.config.quantization_config.scalar.type = "int8"
        mock_collection_info.config.quantization_config.scalar.quantile = 0.99
        mock_collection_info.config.quantization_config.scalar.always_ram = True
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        mock_client.delete_collection = AsyncMock()
        mock_client.create_collection = AsyncMock()
        mock_client.verify_collection_config = AsyncMock()
        
        await ensure_collection(
            mock_client,
            "existing_collection",
            force_recreate=True,
            size=128,
            quantized=True,
        )
        
        # Verify calls
        mock_client.get_collection.assert_called_once_with("existing_collection")
        mock_client.delete_collection.assert_called_once_with("existing_collection")
        mock_client.create_collection.assert_called_once()
        mock_client.verify_collection_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_collection_quantization_missing(self) -> None:
        """Test ensuring collection when quantization missing but expected."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock collection without quantization
        mock_collection_info = MagicMock()
        mock_collection_info.points_count = 100
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params.vectors.size = 128
        mock_collection_info.config.params.vectors.distance = "Cosine"
        mock_collection_info.config.hnsw_config.m = 16
        mock_collection_info.config.hnsw_config.ef_construct = 128
        mock_collection_info.config.quantization_config = None
        
        mock_client.get_collection = AsyncMock(return_value=mock_collection_info)
        mock_client.delete_collection = AsyncMock()
        mock_client.create_collection = AsyncMock()
        mock_client.verify_collection_config = AsyncMock()
        
        await ensure_collection(
            mock_client,
            "missing_quant_collection",
            size=128,
            quantized=True,
        )
        
        # Verify calls
        mock_client.get_collection.assert_called_once_with("missing_quant_collection")
        mock_client.delete_collection.assert_called_once_with("missing_quant_collection")
        mock_client.create_collection.assert_called_once()
        mock_client.verify_collection_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_collection_unexpected_error(self) -> None:
        """Test ensure_collection re-raises unexpected errors."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock unexpected error (not "not found")
        mock_client.get_collection.side_effect = UnexpectedResponse("Permission denied", 403, "Forbidden", {})
        
        with pytest.raises(UnexpectedResponse, match="Permission denied"):
            await ensure_collection(
                mock_client,
                "error_collection",
                size=128,
                quantized=True,
            )

    @pytest.mark.asyncio
    async def test_ensure_collection_uses_settings_embedding_dim(self) -> None:
        """Test ensure_collection uses settings.embedding_dim when size not specified."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        with patch('app.db.qdrant_setup.settings') as mock_settings:
            mock_settings.embedding_dim = 512
            
            # Mock collection not found
            mock_client.get_collection.side_effect = UnexpectedResponse("Collection not found", 404, "Not Found", {})
            mock_client.create_collection = AsyncMock()
            mock_client.verify_collection_config = AsyncMock()
            
            await ensure_collection(
                mock_client,
                "new_collection",
                quantized=True,
            )
            
            # Verify create_collection was called with correct size
            mock_client.create_collection.assert_called_once()
            args, kwargs = mock_client.create_collection.call_args
            assert kwargs["vectors_config"].size == 512


class TestQdrantSetupCLI:
    """Test qdrant_setup CLI functions."""

    def test_build_parser(self) -> None:
        """Test argument parser builds correctly."""
        from app.db.qdrant_setup import build_parser
        
        parser = build_parser()
        
        # Test basic arguments
        args = parser.parse_args(["--collection", "test_collection", "--force"])
        assert args.collection == "test_collection"
        assert args.force is True
        
        # Test quantization options
        args = parser.parse_args(["--no-quantization"])
        assert args.no_quantization is True
        
        args = parser.parse_args(["--enable-quantization-only"])
        assert args.enable_quantization_only is True
        
        args = parser.parse_args(["--verify-only"])
        assert args.verify_only is True

    @pytest.mark.asyncio
    async def test_main_create_collection(self) -> None:
        """Test main function creates collection."""
        from app.db.qdrant_setup import main
        
        # Mock dependencies
        with patch('app.db.qdrant_setup.AsyncQdrantClient') as mock_client_class, \
             patch('app.db.qdrant_setup.ensure_collection') as mock_ensure:
            
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_ensure.return_value = None
            
            # Test successful run
            result = await main()
            assert result == 0
            
            # Verify ensure_collection was called
            mock_ensure.assert_called_once()
            args, kwargs = mock_ensure.call_args
            assert kwargs["collection_name"] == "test_collection"
            assert kwargs["quantized"] is True
            
            # Verify client was closed
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_enable_quantization_only(self) -> None:
        """Test main function enables quantization only."""
        from app.db.qdrant_setup import main
        
        # Mock dependencies
        with patch('app.db.qdrant_setup.AsyncQdrantClient') as mock_client_class, \
             patch('app.db.qdrant_setup.enable_scalar_quantization') as mock_enable:
            
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_enable.return_value = None
            
            # Test enable quantization only
            result = await main()
            assert result == 0
            
            # Verify enable_scalar_quantization was called
            mock_enable.assert_called_once()
            args, kwargs = mock_enable.call_args
            assert kwargs["collection_name"] == "test_collection"
            
            # Verify client was closed
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_verify_only(self) -> None:
        """Test main function verifies collection only."""
        from app.db.qdrant_setup import main
        
        # Mock dependencies
        with patch('app.db.qdrant_setup.AsyncQdrantClient') as mock_client_class, \
             patch('app.db.qdrant_setup.verify_collection_config') as mock_verify:
        
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_verify.return_value = MagicMock()
            
            # Test verify only
            result = await main()
            assert result == 0
            
            # Verify verify_collection_config was called
            mock_verify.assert_called_once()
            args, kwargs = mock_verify.call_args
            assert kwargs["collection_name"] == "test_collection"
            
            # Verify client was closed
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_error_handling(self) -> None:
        """Test main function handles errors correctly."""
        from app.db.qdrant_setup import main
        
        # Mock dependencies
        with patch('app.db.qdrant_setup.AsyncQdrantClient') as mock_client_class, \
             patch('app.db.qdrant_setup.ensure_collection') as mock_ensure:
            
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_ensure.side_effect = Exception("Test error")
            
            # Test error handling
            result = await main()
            assert result == 1
            
            # Verify client was closed even on error
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_client_cleanup_on_error(self) -> None:
        """Test main function cleans up client even if ensure_collection fails."""
        from app.db.qdrant_setup import main
        
        # Mock dependencies
        mock_client = AsyncMock()
        with patch('app.db.qdrant_setup.AsyncQdrantClient') as mock_client_class, \
             patch('app.db.qdrant_setup.ensure_collection') as mock_ensure:
            
            mock_client_class.return_value = mock_client
            mock_ensure.side_effect = Exception("Test error")
            
            # Test error handling
            with pytest.raises(SystemExit, match="1"):
                await main()
            
            # Verify client was closed even on error
            mock_client.close.assert_called_once()